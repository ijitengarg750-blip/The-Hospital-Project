package agent

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"nhooyr.io/websocket"
	"nhooyr.io/websocket/wsjson"
)

func TestOutboundURLRequiresEncryptionOutsideLoopback(t *testing.T) {
	if _, err := outboundWebSocketURL("http://dashboard.hospital.example", "/api/agent/control", false); err == nil {
		t.Fatal("plaintext non-loopback dashboard URL was accepted")
	}
	if _, err := outboundWebSocketURL("https://dashboard.hospital.example:8443", "/api/agent/control", false); err == nil {
		t.Fatal("non-443 outbound URL was accepted")
	}
	if _, err := outboundWebSocketURL("https://192.0.2.10", "/api/agent/control", false); err == nil {
		t.Fatal("IP-literal outbound URL was accepted")
	}
	endpoint, err := outboundWebSocketURL("https://dashboard.hospital.example", "/api/agent/control", false)
	if err != nil || endpoint.String() != "wss://dashboard.hospital.example/api/agent/control" {
		t.Fatalf("endpoint=%v err=%v", endpoint, err)
	}
}

func TestOutboundConnectorOpensSeparateAuthorizedDataStream(t *testing.T) {
	vncAddress, _ := fakeVNC(t)
	ready := make(chan struct{}, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Machine-Code") != "SIRIUS-1" || r.Header.Get("X-Agent-Key") != "agent-secret" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		connection, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer connection.Close(websocket.StatusNormalClosure, "done")
		if r.URL.Path == "/api/agent/control" {
			var heartbeat outboundMessage
			if wsjson.Read(r.Context(), connection, &heartbeat) != nil || heartbeat.Type != "heartbeat" {
				return
			}
			_ = wsjson.Write(r.Context(), connection, outboundMessage{Type: "session_request", SessionID: "session-1", DataTicket: "one-time-ticket"})
			for {
				var message outboundMessage
				if wsjson.Read(r.Context(), connection, &message) != nil {
					return
				}
				if message.Type == "session_ready" && message.SessionID == "session-1" {
					ready <- struct{}{}
					return
				}
			}
		}
		if r.URL.Path == "/api/agent/remote-sessions/session-1/data" && r.Header.Get("X-Session-Ticket") == "one-time-ticket" {
			<-r.Context().Done()
		}
	}))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	state := NewRuntimeState("ui-secret", nil)
	done := make(chan error, 1)
	go func() {
		done <- RunOutbound(ctx, Config{ServerURL: server.URL, MachineCode: "SIRIUS-1", AgentKey: "agent-secret", VNCAddress: vncAddress, VNCPassword: "correct", AllowInsecureLoopback: true}, state, func() string { return "unlocked" })
	}()
	select {
	case <-ready:
	case <-time.After(5 * time.Second):
		t.Fatal("outbound data stream did not become ready")
	}
	if health := state.Snapshot().Components["dashboard_connection"]; health.Status != "healthy" || strings.Contains(health.Error, "5910") {
		t.Fatalf("health=%+v", health)
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
