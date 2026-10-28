package agent

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"nhooyr.io/websocket"
	"nhooyr.io/websocket/wsjson"
)

type outboundMessage struct {
	Type       string `json:"type"`
	State      string `json:"state,omitempty"`
	SessionID  string `json:"session_id,omitempty"`
	DataTicket string `json:"data_ticket,omitempty"`
	Detail     string `json:"detail,omitempty"`
}

type outboundWriter struct {
	mu sync.Mutex
	ws *websocket.Conn
}

func (w *outboundWriter) write(ctx context.Context, message outboundMessage) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	writeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	return wsjson.Write(writeCtx, w.ws, message)
}

func outboundWebSocketURL(serverURL, path string, allowInsecureLoopback bool) (*url.URL, error) {
	base, err := url.Parse(strings.TrimRight(serverURL, "/"))
	if err != nil || base.Host == "" {
		return nil, errors.New("invalid dashboard URL")
	}
	switch base.Scheme {
	case "https":
		if net.ParseIP(base.Hostname()) != nil || strings.EqualFold(base.Hostname(), "localhost") {
			return nil, errors.New("outbound remote access requires a stable DNS hostname")
		}
		if port := base.Port(); port != "" && port != "443" {
			return nil, errors.New("outbound remote access requires TCP 443")
		}
		base.Scheme = "wss"
	case "http":
		host := base.Hostname()
		if !allowInsecureLoopback || (host != "localhost" && host != "127.0.0.1" && host != "::1") {
			return nil, errors.New("outbound remote access requires HTTPS")
		}
		base.Scheme = "ws"
	default:
		return nil, errors.New("dashboard URL must use HTTPS")
	}
	reference, err := url.Parse(path)
	if err != nil {
		return nil, err
	}
	return base.ResolveReference(reference), nil
}

func ValidateOutboundServerURL(serverURL string) error {
	_, err := outboundWebSocketURL(serverURL, "/api/agent/control", false)
	return err
}

func dialOutbound(ctx context.Context, config Config, path, sessionTicket string) (*websocket.Conn, error) {
	endpoint, err := outboundWebSocketURL(config.ServerURL, path, config.AllowInsecureLoopback)
	if err != nil {
		return nil, err
	}
	header := http.Header{}
	header.Set("X-Machine-Code", config.MachineCode)
	header.Set("X-Agent-Key", config.AgentKey)
	if sessionTicket != "" {
		header.Set("X-Session-Ticket", sessionTicket)
	}
	client := &http.Client{Transport: &http.Transport{
		TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS12},
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 10 * time.Second,
	}}
	connection, response, err := websocket.Dial(ctx, endpoint.String(), &websocket.DialOptions{HTTPClient: client, HTTPHeader: header})
	if err != nil {
		if response != nil {
			return nil, fmt.Errorf("outbound WebSocket rejected with HTTP %d: %w", response.StatusCode, err)
		}
		return nil, err
	}
	connection.SetReadLimit(64 * 1024)
	return connection, nil
}

func openAuthenticatedVNC(ctx context.Context, config Config) (net.Conn, error) {
	dialer := net.Dialer{Timeout: 5 * time.Second}
	connection, err := dialer.DialContext(ctx, "tcp", config.VNCAddress)
	if err != nil {
		return nil, fmt.Errorf("connect to local VNC: %w", err)
	}
	_ = connection.SetDeadline(time.Now().Add(5 * time.Second))
	if err = authenticateLocalVNC(connection, config.VNCPassword); err != nil {
		connection.Close()
		return nil, fmt.Errorf("authenticate local VNC: %w", err)
	}
	_ = connection.SetDeadline(time.Time{})
	return connection, nil
}

func runOutboundData(ctx context.Context, config Config, request outboundMessage, writer *outboundWriter, state *RuntimeState, done chan<- string) {
	defer func() { done <- request.SessionID }()
	if request.SessionID == "" || request.DataTicket == "" {
		_ = writer.write(ctx, outboundMessage{Type: "session_failed", SessionID: request.SessionID, Detail: "invalid session request"})
		return
	}
	upstream, err := openAuthenticatedVNC(ctx, config)
	if err != nil {
		state.SetComponent("tightvnc", "degraded", err.Error())
		_ = writer.write(ctx, outboundMessage{Type: "session_failed", SessionID: request.SessionID, Detail: "local VNC unavailable"})
		return
	}
	defer upstream.Close()
	state.SetComponent("tightvnc", "healthy", "")
	path := "/api/agent/remote-sessions/" + url.PathEscape(request.SessionID) + "/data"
	data, err := dialOutbound(ctx, config, path, request.DataTicket)
	if err != nil {
		_ = writer.write(ctx, outboundMessage{Type: "session_failed", SessionID: request.SessionID, Detail: "outbound data stream unavailable"})
		return
	}
	defer data.Close(websocket.StatusNormalClosure, "session ended")
	stream := websocket.NetConn(ctx, data, websocket.MessageBinary)
	defer stream.Close()
	if err = writer.write(ctx, outboundMessage{Type: "session_ready", SessionID: request.SessionID}); err != nil {
		return
	}
	state.SetSession(&Session{ID: request.SessionID, Status: "ready"})
	doneCopy := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(upstream, stream); doneCopy <- struct{}{} }()
	go func() { _, _ = io.Copy(stream, upstream); doneCopy <- struct{}{} }()
	select {
	case <-ctx.Done():
	case <-doneCopy:
	}
}

func runOutboundOnce(ctx context.Context, config Config, client *Client, state *RuntimeState, machineState func() string) error {
	control, err := dialOutbound(ctx, config, "/api/agent/control", "")
	if err != nil {
		return err
	}
	defer control.Close(websocket.StatusNormalClosure, "reconnecting")
	defer state.SetSession(nil)
	writer := &outboundWriter{ws: control}
	state.SetComponent("dashboard_connection", "healthy", "")
	messages := make(chan outboundMessage, 1)
	readErrors := make(chan error, 1)
	go func() {
		for {
			var message outboundMessage
			readCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
			err := wsjson.Read(readCtx, control, &message)
			cancel()
			if err != nil {
				readErrors <- err
				return
			}
			messages <- message
		}
	}()
	heartbeat := time.NewTicker(15 * time.Second)
	poll := time.NewTicker(2 * time.Second)
	defer heartbeat.Stop()
	defer poll.Stop()
	active := make(map[string]context.CancelFunc)
	finished := make(chan string, 4)
	sendHeartbeat := func() error {
		current := machineState()
		state.SetMachineState(current)
		return writer.write(ctx, outboundMessage{Type: "heartbeat", State: current})
	}
	if err = sendHeartbeat(); err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			for _, cancel := range active {
				cancel()
			}
			return nil
		case err = <-readErrors:
			for _, cancel := range active {
				cancel()
			}
			return err
		case <-heartbeat.C:
			if err = sendHeartbeat(); err != nil {
				return err
			}
		case message := <-messages:
			switch message.Type {
			case "heartbeat_ack":
			case "session_request":
				if _, exists := active[message.SessionID]; exists {
					continue
				}
				sessionCtx, cancel := context.WithCancel(ctx)
				active[message.SessionID] = cancel
				go runOutboundData(sessionCtx, config, message, writer, state, finished)
			case "session_end":
				if cancel := active[message.SessionID]; cancel != nil {
					cancel()
				}
			}
		case sessionID := <-finished:
			delete(active, sessionID)
			state.ClearSession(sessionID)
		case <-poll.C:
			if pending := state.PendingEnd(); pending != "" {
				if cancel := active[pending]; cancel != nil {
					cancel()
				}
				if _, err := client.End(pending); err == nil {
					state.clearPendingEnd(pending)
				}
			}
		}
	}
}

func RunOutbound(ctx context.Context, config Config, state *RuntimeState, machineState func() string) error {
	client := NewClient(config)
	delay := time.Second
	for {
		err := runOutboundOnce(ctx, config, client, state, machineState)
		if ctx.Err() != nil {
			return nil
		}
		state.SetComponent("dashboard_connection", "degraded", err.Error())
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
		if delay < 30*time.Second {
			delay *= 2
		}
		if delay > 30*time.Second {
			delay = 30 * time.Second
		}
	}
}
