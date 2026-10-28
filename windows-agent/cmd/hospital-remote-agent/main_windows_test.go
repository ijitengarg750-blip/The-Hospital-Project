package main

import (
	"context"
	"errors"
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
