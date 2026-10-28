package agent

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRequestedSessionIsNotMarkedReadyWhenVNCIsUnavailable(t *testing.T) {
	readyCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/agent/remote-sessions/session-1/ready" {
			readyCalls++
		}
	}))
	defer server.Close()
	client := NewClient(Config{ServerURL: server.URL, MachineCode: "SIRIUS-1", AgentKey: "agent-secret"})

	_, err := prepareRequestedSession(context.Background(), client, Session{ID: "session-1", Status: "requested"}, func(context.Context) error {
		return errors.New("VNC unavailable")
	}, NewTunnelAuthorizer("agent-secret"))

	if err == nil || readyCalls != 0 {
		t.Fatalf("err=%v readyCalls=%d", err, readyCalls)
	}
}

func TestRequestedSessionIsMarkedReadyAfterVNCCheck(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(Session{ID: "session-1", Status: "ready"})
	}))
	defer server.Close()
	client := NewClient(Config{ServerURL: server.URL, MachineCode: "SIRIUS-1", AgentKey: "agent-secret"})

	session, err := prepareRequestedSession(context.Background(), client, Session{ID: "session-1", Status: "requested"}, func(context.Context) error { return nil }, NewTunnelAuthorizer("agent-secret"))

	if err != nil || session.Status != "ready" {
		t.Fatalf("session=%+v err=%v", session, err)
	}
}

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
	state := NewRuntimeState("ui-secret", NewTunnelAuthorizer("agent-secret"))
	request := httptest.NewRequest(http.MethodGet, "/state", nil)
	recorder := httptest.NewRecorder()
	state.Handler(nil).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d", recorder.Code)
	}
}

func TestLocalControlRejectsWrongHTTPMethod(t *testing.T) {
	state := NewRuntimeState("ui-secret", NewTunnelAuthorizer("agent-secret"))
	request := httptest.NewRequest(http.MethodPost, "/state", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	recorder := httptest.NewRecorder()
	state.Handler(nil).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status=%d", recorder.Code)
	}
}

func TestOfflineLocalEndRevokesFirstAndReturnsSuccess(t *testing.T) {
	authorizer := NewTunnelAuthorizer("agent-secret")
	authorizer.Arm("session-1")
	state := NewRuntimeState("ui-secret", authorizer)
	state.SetSession(&Session{ID: "session-1", Status: "connected"})
	request := httptest.NewRequest(http.MethodPost, "/end", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	recorder := httptest.NewRecorder()

	state.Handler(nil).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if authorizer.IsArmed("session-1") {
		t.Fatal("session remains authorized after local end")
	}
	if state.PendingEnd() != "session-1" {
		t.Fatalf("pending end=%q", state.PendingEnd())
	}
}

func TestOfflineLocalEndSurvivesRuntimeRestart(t *testing.T) {
	stored := ""
	state := NewRuntimeState("ui-secret", nil)
	state.SetPendingEndPersistence("", func(sessionID string) error { stored = sessionID; return nil })
	state.SetSession(&Session{ID: "session-1", Status: "connected"})
	request := httptest.NewRequest(http.MethodPost, "/end", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	recorder := httptest.NewRecorder()

	state.Handler(nil).ServeHTTP(recorder, request)
	restarted := NewRuntimeState("ui-secret", nil)
	restarted.SetPendingEndPersistence(stored, func(sessionID string) error { stored = sessionID; return nil })

	if recorder.Code != http.StatusNoContent || restarted.PendingEnd() != "session-1" {
		t.Fatalf("status=%d persisted=%q", recorder.Code, restarted.PendingEnd())
	}
}

func TestOlderDataCompletionDoesNotClearNewerSession(t *testing.T) {
	state := NewRuntimeState("ui-secret", nil)
	state.SetSession(&Session{ID: "session-2", Status: "ready"})

	state.ClearSession("session-1")

	if session := state.Snapshot().Session; session == nil || session.ID != "session-2" {
		t.Fatalf("session=%+v", session)
	}
}

func TestOfflineLocalEndDoesNotWaitForDashboardTimeout(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		time.Sleep(time.Second)
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer server.Close()
	authorizer := NewTunnelAuthorizer("agent-secret")
	authorizer.Arm("session-1")
	state := NewRuntimeState("ui-secret", authorizer)
	state.SetSession(&Session{ID: "session-1", Status: "connected"})
	request := httptest.NewRequest(http.MethodPost, "/end", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	recorder := httptest.NewRecorder()
	started := time.Now()

	state.Handler(NewClient(Config{ServerURL: server.URL})).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNoContent || time.Since(started) > 250*time.Millisecond || calls != 0 {
		t.Fatalf("status=%d elapsed=%s dashboard_calls=%d", recorder.Code, time.Since(started), calls)
	}
}

func TestOfflineLocalEndCannotBeOverwrittenByPollingRace(t *testing.T) {
	authorizer := NewTunnelAuthorizer("agent-secret")
	authorizer.Arm("session-1")
	state := NewRuntimeState("ui-secret", authorizer)
	state.SetSession(&Session{ID: "session-1", Status: "connected"})
	request := httptest.NewRequest(http.MethodPost, "/end", nil)
	request.Header.Set("Authorization", "Bearer ui-secret")
	state.Handler(nil).ServeHTTP(httptest.NewRecorder(), request)

	if state.setAuthorizedSession(&Session{ID: "session-1", Status: "connected"}, authorizer) || state.Snapshot().Session != nil {
		t.Fatal("locally ended session was restored by a stale dashboard poll")
	}
}

func TestSessionCannotBecomeReadyWhileSecureTunnelIsDegraded(t *testing.T) {
	state := NewRuntimeState("ui-secret", NewTunnelAuthorizer("agent-secret"))
	state.SetComponent("secure_tunnel", "degraded", "bind failed")
	vncChecks := 0

	err := state.checkSessionReadiness(context.Background(), func(context.Context) error {
		vncChecks++
		return nil
	})

	if err == nil || vncChecks != 0 {
		t.Fatalf("err=%v vnc_checks=%d", err, vncChecks)
	}
}

func TestComponentFailureIsReportedWithoutClearingUnrelatedSession(t *testing.T) {
	state := NewRuntimeState("ui-secret", NewTunnelAuthorizer("agent-secret"))
	state.SetSession(&Session{ID: "session-1", Status: "ready"})
	state.SetComponent("tightvnc", "degraded", "connection refused")

	snapshot := state.Snapshot()

	if snapshot.Session == nil || snapshot.Session.ID != "session-1" {
		t.Fatal("component failure cleared an unrelated session")
	}
	health := snapshot.Components["tightvnc"]
	if health.Status != "degraded" || health.Error != "connection refused" {
		t.Fatalf("health=%+v", health)
	}
}

func TestNotificationUIHealthBecomesDegradedWhenHeartbeatIsStale(t *testing.T) {
	state := NewRuntimeState("ui-secret", NewTunnelAuthorizer("agent-secret"))
	state.SetComponent("notification_ui", "healthy", "")
	state.mu.Lock()
	health := state.snapshot.Components["notification_ui"]
	health.UpdatedAt = time.Now().Add(-time.Minute)
	state.snapshot.Components["notification_ui"] = health
	state.mu.Unlock()

	health = state.Snapshot().Components["notification_ui"]
	if health.Status != "degraded" || health.Error == "" {
		t.Fatalf("health=%+v", health)
	}
}

func TestConnectorRestartEndsServerSessionInsteadOfSilentlyReauthorizing(t *testing.T) {
	ended := make(chan struct{}, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "POST /api/agent/heartbeat":
			w.WriteHeader(http.StatusOK)
		case "GET /api/agent/remote-sessions/next":
			_ = json.NewEncoder(w).Encode(Session{ID: "old-session", Status: "connected"})
		case "POST /api/agent/remote-sessions/old-session/end":
			select {
			case ended <- struct{}{}:
			default:
			}
			_ = json.NewEncoder(w).Encode(Session{ID: "old-session", Status: "ended"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	authorizer := NewTunnelAuthorizer("agent-secret")
	state := NewRuntimeState("ui-secret", authorizer)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- Run(ctx, NewClient(Config{ServerURL: server.URL, MachineCode: "SIRIUS-1", AgentKey: "agent-secret"}), state, func() string { return "unlocked" }, func(context.Context) error { return nil }, authorizer)
	}()
	select {
	case <-ended:
	case <-time.After(4 * time.Second):
		t.Fatal("stale connected session was not ended after connector restart")
	}
	if authorizer.Owns("old-session") || state.Snapshot().Session != nil {
		t.Fatal("old session was silently reauthorized or reported active")
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
