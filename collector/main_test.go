package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPlaintextCollectorCredentialIsRejectedInJSON(t *testing.T) {
	var cfg Config
	for _, field := range []string{"collector_key", "api_key"} {
		if err := json.Unmarshal([]byte(`{"machine_code":"TEST-1","`+field+`":"must-not-load","collector_credential_file":"credential.dat"}`), &cfg); err == nil {
			t.Fatalf("plaintext %s was accepted", field)
		}
	}
}

func TestCollectorWritesIndependentHealthWithoutCredential(t *testing.T) {
	path := filepath.Join(t.TempDir(), "health.json")
	state := State{Uploaded: map[string]string{"exam.dcm": "digest"}, Failures: map[string]Failure{"queued.dcm": {Attempts: 2}}}

	if err := saveHealth(path, state, "upload failed; see collector log"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var health CollectorHealth
	if err = json.Unmarshal(raw, &health); err != nil {
		t.Fatal(err)
	}
	if health.Status != "degraded" || health.LastError == "" || health.UploadedFiles != 1 || health.QueuedFailures != 1 || strings.Contains(string(raw), "collector_key") {
		t.Fatalf("health=%+v raw=%s", health, raw)
	}
}

func TestInterruptedUploadRecoversAfterRestartWithoutReuploadingSuccess(t *testing.T) {
	directory := t.TempDir()
	source := filepath.Join(directory, "exam.dcm")
	if err := os.WriteFile(source, []byte("disposable transport fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		if r.Header.Get("X-Collector-Key") != "machine-collector-key" {
			t.Error("per-machine collector credential missing from upload")
		}
		if r.Header.Get("X-Machine-Code") != "TEST-1" {
			t.Error("machine identity missing from upload header")
		}
		if attempts == 1 {
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			connection.Close()
			return
		}
		if err := r.ParseMultipartForm(1024); err != nil {
			t.Error(err)
			return
		}
		defer r.MultipartForm.RemoveAll()
		if r.FormValue("machine_code") != "TEST-1" {
			t.Error("machine identity lost during retry")
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	cfg := Config{MachineCode: "TEST-1", ServerURL: server.URL, CollectorKey: "machine-collector-key", Folders: []string{directory}, Extensions: []string{".dcm"}, StableChecks: 2, StateFile: filepath.Join(directory, "state.json")}
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}
	scan(cfg, &state)
	if attempts != 1 || len(state.Uploaded) != 0 || len(state.Failures) != 1 {
		t.Fatalf("interrupted transfer was not retained for retry: %#v", state)
	}
	restarted := State{}
	if err := loadJSON(cfg.StateFile, &restarted); err != nil {
		t.Fatal(err)
	}
	key := strings.ToLower(filepath.Clean(source))
	failure := restarted.Failures[key]
	failure.NextAttempt = time.Now().Add(-time.Second)
	restarted.Failures[key] = failure
	scan(cfg, &restarted)
	scan(cfg, &restarted)
	if attempts != 2 || len(restarted.Uploaded) != 1 || len(restarted.Failures) != 0 {
		t.Fatalf("recovery failed or successful file was reuploaded: attempts=%d state=%#v", attempts, restarted)
	}
}

func TestEmptyMissingAndGrowingFilesAreNotStable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "incomplete.dcm")
	if isStable(path, 2) {
		t.Fatal("missing file accepted")
	}
	if err := os.WriteFile(path, nil, 0600); err != nil {
		t.Fatal(err)
	}
	if isStable(path, 2) {
		t.Fatal("empty file accepted")
	}
	if err := os.WriteFile(path, []byte("partial"), 0600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		time.Sleep(500 * time.Millisecond)
		done <- os.WriteFile(path, []byte("partial with more bytes"), 0600)
	}()
	stable := isStable(path, 2)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if stable {
		t.Fatal("file growing during the observation window was accepted")
	}
}

func TestHTTPClientFailuresAreClassifiedForRetry(t *testing.T) {
	cases := []struct {
		name      string
		err       error
		permanent bool
	}{
		{"unauthorized key during rotation", &uploadError{StatusCode: 401}, false},
		{"unknown machine", &uploadError{StatusCode: 422}, true},
		{"backend failure", &uploadError{StatusCode: 502}, false},
		{"connection reset", errors.New("connection reset by peer"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isPermanentFailure(tc.err); got != tc.permanent {
				t.Fatalf("isPermanentFailure(%v) = %v, want %v", tc.err, got, tc.permanent)
			}
		})
	}
}

func TestServerURLRequiresTLSExceptForLoopbackDevelopment(t *testing.T) {
	for _, candidate := range []string{"https://dashboard.hospital.example", "http://localhost:8080", "http://127.0.0.1:8080"} {
		if err := validateServerURL(candidate); err != nil {
			t.Fatalf("expected %s to be accepted: %v", candidate, err)
		}
	}
	for _, candidate := range []string{"http://192.168.1.10:8080", "http://dashboard.hospital.example", "not-a-url"} {
		if err := validateServerURL(candidate); err == nil {
			t.Fatalf("expected %s to be rejected", candidate)
		}
	}
}

func TestUploadDoesNotForwardCredentialAcrossRedirects(t *testing.T) {
	forwarded := 0
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded++
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	path := filepath.Join(t.TempDir(), "report.dcm")
	if err := os.WriteFile(path, []byte("test"), 0600); err != nil {
		t.Fatal(err)
	}
	err := upload(Config{MachineCode: "TEST-1", ServerURL: redirect.URL, CollectorKey: "secret"}, path)
	var responseError *uploadError
	if !errors.As(err, &responseError) || responseError.StatusCode != http.StatusTemporaryRedirect || forwarded != 0 {
		t.Fatalf("redirect was followed or not rejected: err=%v forwarded=%d", err, forwarded)
	}
}

func TestFailureStateUsesBoundedExponentialBackoff(t *testing.T) {
	now := time.Date(2026, 10, 22, 5, 0, 0, 0, time.UTC)
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}

	state.recordFailure("c:\\exports\\exam.dcm", "digest", errors.New("timeout"), now)
	first := state.Failures["c:\\exports\\exam.dcm"]
	if first.Attempts != 1 || !first.NextAttempt.After(now) || first.Permanent {
		t.Fatalf("unexpected first failure state: %#v", first)
	}

	state.recordFailure("c:\\exports\\exam.dcm", "digest", errors.New("timeout"), now)
	second := state.Failures["c:\\exports\\exam.dcm"]
	if second.Attempts != 2 || second.NextAttempt.Sub(now) <= first.NextAttempt.Sub(now) {
		t.Fatalf("backoff did not grow: first=%#v second=%#v", first, second)
	}
}

func TestPermanentFailureIsNotRetriedUntilTheFileChanges(t *testing.T) {
	now := time.Date(2026, 10, 22, 5, 0, 0, 0, time.UTC)
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}
	state.recordFailure("c:\\exports\\exam.dcm", "digest", &uploadError{StatusCode: 422}, now)
	if state.shouldAttempt("c:\\exports\\exam.dcm", "digest", now.Add(24*time.Hour)) {
		t.Fatal("permanent failure should not be retried without a source change")
	}
	if !state.shouldAttempt("c:\\exports\\exam.dcm", "new-digest", now.Add(24*time.Hour)) {
		t.Fatal("a changed source must be eligible for a new upload attempt")
	}
}

func TestCredentialFailureIsRetriedAfterBackoff(t *testing.T) {
	now := time.Date(2026, 10, 22, 5, 0, 0, 0, time.UTC)
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}
	state.recordFailure("c:\\exports\\exam.dcm", "digest", &uploadError{StatusCode: 401}, now)
	failure := state.Failures["c:\\exports\\exam.dcm"]
	if failure.Permanent || !state.shouldAttempt("c:\\exports\\exam.dcm", "digest", failure.NextAttempt) {
		t.Fatal("credential rotation must not permanently suppress a queued report")
	}
}

func TestPersistedCredentialFailuresBecomeRetryableAfterUpgrade(t *testing.T) {
	state := State{Failures: map[string]Failure{
		"unauthorized": {Permanent: true, LastError: "server returned HTTP 401: old key"},
		"forbidden":    {Permanent: true, LastError: "server returned HTTP 403: old key"},
		"invalid":      {Permanent: true, LastError: "server returned HTTP 422: invalid report"},
	}}
	migrateCredentialFailures(&state)
	if state.Failures["unauthorized"].Permanent || state.Failures["forbidden"].Permanent {
		t.Fatal("persisted credential failures remained permanently suppressed")
	}
	if !state.Failures["invalid"].Permanent {
		t.Fatal("non-credential permanent failure was incorrectly reclassified")
	}
}

func TestFailedUploadStateSurvivesCollectorRestart(t *testing.T) {
	now := time.Date(2026, 10, 22, 5, 0, 0, 0, time.UTC)
	statePath := filepath.Join(t.TempDir(), "state.json")
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}
	state.recordFailure("c:\\exports\\exam.dcm", "digest", errors.New("connection reset"), now)
	if err := saveState(statePath, state); err != nil {
		t.Fatal(err)
	}

	restarted := State{}
	if err := loadJSON(statePath, &restarted); err != nil {
		t.Fatal(err)
	}
	if restarted.shouldAttempt("c:\\exports\\exam.dcm", "digest", now.Add(time.Second)) {
		t.Fatal("restart lost retry backoff state")
	}
	if err := os.Remove(statePath); err != nil {
		t.Fatal(err)
	}
}
