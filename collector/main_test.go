package main

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestHTTPClientFailuresAreClassifiedForRetry(t *testing.T) {
	cases := []struct {
		name      string
		err       error
		permanent bool
	}{
		{"unauthorized key", &uploadError{StatusCode: 401}, true},
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
	state.recordFailure("c:\\exports\\exam.dcm", "digest", &uploadError{StatusCode: 401}, now)
	if state.shouldAttempt("c:\\exports\\exam.dcm", "digest", now.Add(24*time.Hour)) {
		t.Fatal("permanent failure should not be retried without a source change")
	}
	if !state.shouldAttempt("c:\\exports\\exam.dcm", "new-digest", now.Add(24*time.Hour)) {
		t.Fatal("a changed source must be eligible for a new upload attempt")
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
