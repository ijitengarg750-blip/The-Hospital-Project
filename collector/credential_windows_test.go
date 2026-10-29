//go:build windows

package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestCollectorCredentialIsDPAPIProtectedAndRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "collector-credential.dat")
	want := "machine-collector-secret-at-least-32-bytes"
	if err := protectCollectorKey(path, want); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte(want)) {
		t.Fatal("collector credential was stored in plaintext")
	}
	got, err := loadCollectorKey(path)
	if err != nil || got != want {
		t.Fatalf("got=%q err=%v", got, err)
	}
}

func TestCollectorCredentialACLFailureLeavesNoBlob(t *testing.T) {
	path := filepath.Join(t.TempDir(), "collector-credential.dat")
	err := createCollectorCredential(path, "machine-collector-secret-at-least-32-bytes", `.\AccountThatMustNotExist`)
	if err == nil {
		t.Fatal("invalid service account unexpectedly accepted")
	}
	if _, statErr := os.Stat(path); !os.IsNotExist(statErr) {
		t.Fatalf("credential blob remained after ACL failure: %v", statErr)
	}
	matches, globErr := filepath.Glob(path + ".*.tmp")
	if globErr != nil || len(matches) != 0 {
		t.Fatalf("temporary credential files remained: %v err=%v", matches, globErr)
	}
}
