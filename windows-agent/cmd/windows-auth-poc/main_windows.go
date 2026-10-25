package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
	"hospital/remote-agent/agent"
)

var logonUser = windows.NewLazySystemDLL("advapi32.dll").NewProc("LogonUserW")

func validate(username, domain, password string) error {
	user, err := windows.UTF16PtrFromString(username)
	if err != nil {
		return err
	}
	domainPointer, err := windows.UTF16PtrFromString(domain)
	if err != nil {
		return err
	}
	secret, err := windows.UTF16FromString(password)
	if err != nil {
		return err
	}
	defer func() {
		for index := range secret {
			secret[index] = 0
		}
	}()
	var token windows.Handle
	ok, _, callErr := logonUser.Call(uintptr(unsafe.Pointer(user)), uintptr(unsafe.Pointer(domainPointer)), uintptr(unsafe.Pointer(&secret[0])), 2, 0, uintptr(unsafe.Pointer(&token)))
	if ok == 0 {
		return callErr
	}
	return windows.CloseHandle(token)
}

func main() {
	username := flag.String("username", "", "dedicated Windows account")
	domain := flag.String("domain", ".", "account domain, or . for a local account")
	store := flag.String("store", "", "optional DPAPI-protected output path")
	flag.Parse()
	if *username == "" {
		fmt.Fprintln(os.Stderr, "--username is required")
		os.Exit(2)
	}
	fmt.Fprint(os.Stderr, "Windows password: ")
	stdin := windows.Handle(os.Stdin.Fd())
	var mode uint32
	console := windows.GetConsoleMode(stdin, &mode) == nil
	if console {
		_ = windows.SetConsoleMode(stdin, mode&^windows.ENABLE_ECHO_INPUT)
		defer windows.SetConsoleMode(stdin, mode)
	}
	password, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if console {
		fmt.Fprintln(os.Stderr)
	}
	password = strings.TrimSpace(password)
	if err != nil && password == "" {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err = validate(*username, *domain, password); err != nil {
		fmt.Fprintln(os.Stderr, "Windows rejected the credential:", err)
		os.Exit(1)
	}
	if *store != "" {
		if err = agent.SaveWindowsCredential(*store, agent.WindowsCredential{Username: *username, Domain: *domain, Password: password}); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err = exec.Command("icacls", *store, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F").Run(); err != nil {
			fmt.Fprintln(os.Stderr, "credential stored but ACL hardening failed:", err)
			os.Exit(1)
		}
	}
	fmt.Println("Windows accepted the dedicated account for interactive logon.")
}
