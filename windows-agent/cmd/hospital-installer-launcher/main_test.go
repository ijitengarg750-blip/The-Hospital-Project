package main

import (
	"path/filepath"
	"reflect"
	"testing"
)

func TestPowerShellInvocation(t *testing.T) {
	installDir := `C:\Program Files\Hospital Diagnostic Components`
	tests := []struct {
		name         string
		arguments    []string
		wantScript   string
		wantTrailing []string
		wantError    bool
	}{
		{
			name:       "configure",
			arguments:  []string{"configure"},
			wantScript: filepath.Join(installDir, "Configure-Installation.ps1"),
		},
		{
			name:         "uninstall",
			arguments:    []string{"uninstall", "{11111111-2222-3333-4444-555555555555}"},
			wantScript:   filepath.Join(installDir, "Remove-Installation.ps1"),
			wantTrailing: []string{"-Prompt", "-ProductCode", "{11111111-2222-3333-4444-555555555555}"},
		},
		{name: "rejects unknown action", arguments: []string{"shell"}, wantError: true},
		{name: "rejects malformed product code", arguments: []string{"uninstall", "not-a-guid"}, wantError: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := powerShellArguments(installDir, test.arguments)
			if test.wantError {
				if err == nil {
					t.Fatal("expected an error")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			want := []string{"-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "AllSigned", "-File", test.wantScript}
			want = append(want, test.wantTrailing...)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected PowerShell arguments\n got: %#v\nwant: %#v", got, want)
			}
		})
	}
}
