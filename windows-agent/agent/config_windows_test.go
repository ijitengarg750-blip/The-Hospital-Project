package agent

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestConfigIsDPAPIProtectedAndRoundTripsOnThisWindowsMachine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.dat")
	want := Config{ServerURL: "https://dashboard.test", MachineCode: "SIRIUS-1", AgentKey: "secret-agent-key", UIToken: "ui-token"}
	if err := SaveConfig(path, want); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte(want.AgentKey)) {
		t.Fatal("agent key was stored in plaintext")
	}
	got, err := LoadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("got=%+v want=%+v", got, want)
	}
}

func TestPendingEndIsDPAPIProtectedAndRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pending.dat")
	if err := SavePendingEnd(path, "session-1"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("session-1")) {
		t.Fatal("pending end session was stored in plaintext")
	}
	got, err := LoadPendingEnd(path)
	if err != nil || got != "session-1" {
		t.Fatalf("got=%q err=%v", got, err)
	}
}
