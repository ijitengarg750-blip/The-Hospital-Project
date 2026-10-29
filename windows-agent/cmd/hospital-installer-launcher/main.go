package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
)

var productCodePattern = regexp.MustCompile(`^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$`)

func powerShellArguments(installDir string, arguments []string) ([]string, error) {
	base := []string{"-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "AllSigned", "-File"}
	switch {
	case len(arguments) == 1 && arguments[0] == "configure":
		return append(base, filepath.Join(installDir, "Configure-Installation.ps1")), nil
	case len(arguments) == 2 && arguments[0] == "uninstall" && productCodePattern.MatchString(arguments[1]):
		return append(base,
			filepath.Join(installDir, "Remove-Installation.ps1"),
			"-Prompt", "-ProductCode", arguments[1],
		), nil
	default:
		return nil, errors.New("invalid installer launcher arguments")
	}
}

func run(arguments []string) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	powerShellArguments, err := powerShellArguments(filepath.Dir(executable), arguments)
	if err != nil {
		return err
	}
	systemRoot := os.Getenv("SystemRoot")
	if systemRoot == "" {
		return errors.New("SystemRoot is not set")
	}
	powerShell := filepath.Join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	return exec.Command(powerShell, powerShellArguments...).Run()
}

func main() {
	if run(os.Args[1:]) != nil {
		os.Exit(1)
	}
}
