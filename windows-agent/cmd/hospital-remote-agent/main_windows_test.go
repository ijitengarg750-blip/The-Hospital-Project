package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestComponentSupervisorRecoversAfterOnlyThatComponentStops(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	first := make(chan error, 1)
	first <- errors.New("deliberate crash")
	second := make(chan error)
	restarts := 0
	start := func() (<-chan error, error) {
		restarts++
		return second, nil
	}
	events := make(chan componentEvent, 4)

	go superviseComponent(ctx, "secure_tunnel", first, start, 10*time.Millisecond, events)

	degraded := <-events
	recovered := <-events
	if degraded.status != "degraded" || recovered.status != "healthy" || restarts != 1 {
		t.Fatalf("degraded=%+v recovered=%+v restarts=%d", degraded, recovered, restarts)
	}
}

func TestPreflightCommandHasNoInstallationSideEffects(t *testing.T) {
	if err := run([]string{"preflight"}); err != nil {
		t.Fatalf("preflight command failed: %v", err)
	}
	if err := run([]string{"preflight", "unexpected"}); err == nil {
		t.Fatal("preflight accepted an unexpected argument")
	}
}

func TestRepairServiceRejectsUnexpectedArguments(t *testing.T) {
	if err := run([]string{"repair-service", "one", "two"}); err == nil {
		t.Fatal("repair-service accepted an unexpected argument")
	}
	if err := run([]string{"repair-service-if-configured", "one", "two"}); err == nil {
		t.Fatal("repair-service-if-configured accepted an unexpected argument")
	}
}

func TestServiceBinaryPathQuotesInstalledExecutable(t *testing.T) {
	command := serviceBinaryPath(`C:\Program Files\Hospital Diagnostics\hospital-remote-agent.exe`, `C:\ProgramData\HospitalRemoteAgent\service.dat`)
	if !strings.HasPrefix(command, `"C:\Program Files\Hospital Diagnostics\hospital-remote-agent.exe" service `) {
		t.Fatalf("service command did not quote executable: %s", command)
	}
}
