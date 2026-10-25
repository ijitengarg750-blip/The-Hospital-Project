package agent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClientHeartbeatClaimsSessionMessagesAndEnds(t *testing.T) {
	var ended bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Machine-Code") != "SIRIUS-1" || r.Header.Get("X-Agent-Key") != "agent-secret" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		switch r.Method + " " + r.URL.Path {
		case "POST /api/agent/heartbeat":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"state":"locked"}`))
		case "GET /api/agent/remote-sessions/next":
			_ = json.NewEncoder(w).Encode(Session{ID: "session-1", Status: "requested", DoctorUsername: "doctor"})
		case "POST /api/agent/remote-sessions/session-1/ready":
			_ = json.NewEncoder(w).Encode(Session{ID: "session-1", Status: "ready"})
		case "GET /api/agent/remote-sessions/session-1/messages":
			_ = json.NewEncoder(w).Encode([]Message{{ID: 7, Sender: "doctor", Body: "Hello"}})
		case "POST /api/agent/remote-sessions/session-1/end":
			ended = true
			_ = json.NewEncoder(w).Encode(Session{ID: "session-1", Status: "ended"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	client := NewClient(Config{ServerURL: server.URL, MachineCode: "SIRIUS-1", AgentKey: "agent-secret"})
	if err := client.Heartbeat("locked"); err != nil {
		t.Fatal(err)
	}
	session, err := client.NextSession()
	if err != nil || session.ID != "session-1" {
		t.Fatalf("session=%+v err=%v", session, err)
	}
	if _, err = client.MarkReady(session.ID); err != nil {
		t.Fatal(err)
	}
	messages, err := client.Messages(session.ID, 0)
	if err != nil || len(messages) != 1 || messages[0].Body != "Hello" {
		t.Fatalf("messages=%+v err=%v", messages, err)
	}
	if _, err = client.End(session.ID); err != nil || !ended {
		t.Fatalf("ended=%v err=%v", ended, err)
	}
}

func TestLocalControlRejectsMissingToken(t *testing.T) {
	state := NewRuntimeState("ui-secret")
	request := httptest.NewRequest(http.MethodGet, "/state", nil)
	recorder := httptest.NewRecorder()
	state.Handler(nil).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d", recorder.Code)
	}
}

func TestLocalControlRejectsWrongHTTPMethod(t *testing.T) {
	state := NewRuntimeState("ui-secret")
	request := httptest.NewRequest(http.MethodPost, "/state", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	recorder := httptest.NewRecorder()
	state.Handler(nil).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status=%d", recorder.Code)
	}
}
