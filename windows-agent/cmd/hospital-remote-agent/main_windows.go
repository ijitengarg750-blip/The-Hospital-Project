package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
	"hospital/remote-agent/agent"
)

const serviceName = "HospitalRemoteAgent"

type serviceHandler struct{ configPath string }

func (handler *serviceHandler) Execute(_ []string, requests <-chan svc.ChangeRequest, statuses chan<- svc.Status) (bool, uint32) {
	statuses <- svc.Status{State: svc.StartPending}
	config, err := agent.LoadConfig(handler.configPath)
	if err != nil {
		return true, 1
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	windowsState := agent.NewWindowsState()
	client := agent.NewClient(config)
	runtimeState := agent.NewRuntimeState(config.UIToken)
	go runLocalControl(ctx, runtimeState.Handler(client))
	go agent.Run(ctx, client, runtimeState, windowsState.Get)
	statuses <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown | svc.AcceptSessionChange}
	for request := range requests {
		switch request.Cmd {
		case svc.Stop, svc.Shutdown:
			statuses <- svc.Status{State: svc.StopPending}
			cancel()
			return false, 0
		case svc.SessionChange:
			switch request.EventType {
			case windows.WTS_SESSION_LOCK:
				windowsState.Set("locked")
			case windows.WTS_SESSION_UNLOCK, windows.WTS_SESSION_LOGON:
				windowsState.Set("unlocked")
			case windows.WTS_SESSION_LOGOFF:
				windowsState.Set("signed_out")
			}
		}
	}
	return false, 0
}

func runLocalControl(ctx context.Context, handler http.Handler) {
	listener, err := net.Listen("tcp", "127.0.0.1:19377")
	if err != nil {
		return
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 3 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	_ = server.Serve(listener)
}

func randomToken() string {
	value := make([]byte, 32)
	_, _ = rand.Read(value)
	return base64.RawURLEncoding.EncodeToString(value)
}

func dataDirectory() string {
	root := os.Getenv("ProgramData")
	if root == "" {
		root = `C:\ProgramData`
	}
	return filepath.Join(root, "HospitalRemoteAgent")
}

func install(args []string) error {
	flags := flag.NewFlagSet("install", flag.ContinueOnError)
	server := flags.String("server", "", "dashboard base URL (HTTPS required outside localhost)")
	machine := flags.String("machine", "", "registered machine code")
	uiUser := flags.String("ui-user", "", "Windows account allowed to run the notification UI")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *server == "" || *machine == "" || *uiUser == "" {
		return errors.New("--server, --machine, and --ui-user are required")
	}
	if !strings.HasPrefix(*server, "https://") && !strings.HasPrefix(*server, "http://localhost") && !strings.HasPrefix(*server, "http://127.0.0.1") {
		return errors.New("server must use HTTPS")
	}
	fmt.Fprint(os.Stderr, "Agent key: ")
	stdin := windows.Handle(os.Stdin.Fd())
	var consoleMode uint32
	consoleInput := windows.GetConsoleMode(stdin, &consoleMode) == nil
	if consoleInput {
		_ = windows.SetConsoleMode(stdin, consoleMode&^windows.ENABLE_ECHO_INPUT)
		defer windows.SetConsoleMode(stdin, consoleMode)
	}
	key, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if consoleInput {
		fmt.Fprintln(os.Stderr)
	}
	if err != nil && len(key) == 0 {
		return err
	}
	key = strings.TrimSpace(key)
	if len(key) < 32 {
		return errors.New("agent key is too short")
	}
	directory := dataDirectory()
	if err := os.MkdirAll(directory, 0750); err != nil {
		return err
	}
	config := agent.Config{ServerURL: *server, MachineCode: *machine, AgentKey: key, UIToken: randomToken()}
	serviceConfig := filepath.Join(directory, "service.dat")
	uiConfig := filepath.Join(directory, "ui.dat")
	if err := agent.SaveConfig(serviceConfig, config); err != nil {
		return err
	}
	if err := agent.SaveConfig(uiConfig, agent.Config{UIToken: config.UIToken}); err != nil {
		return err
	}
	if err := exec.Command("icacls", serviceConfig, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F").Run(); err != nil {
		return fmt.Errorf("secure service config ACL: %w", err)
	}
	if err := exec.Command("icacls", uiConfig, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F", *uiUser+":R").Run(); err != nil {
		return fmt.Errorf("secure UI config ACL: %w", err)
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	service, err := manager.CreateService(serviceName, executable, mgr.Config{DisplayName: "Hospital Remote Diagnostic Agent", Description: "Authorizes and controls audited diagnostic remote sessions.", StartType: mgr.StartAutomatic}, "service", serviceConfig)
	if err != nil {
		return err
	}
	defer service.Close()
	return service.Start()
}

func uninstall() error {
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	service, err := manager.OpenService(serviceName)
	if err != nil {
		return err
	}
	defer service.Close()
	_, _ = service.Control(svc.Stop)
	return service.Delete()
}

func runConsole(configPath string) error {
	config, err := agent.LoadConfig(configPath)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	state := agent.NewRuntimeState(config.UIToken)
	go runLocalControl(ctx, state.Handler(agent.NewClient(config)))
	return agent.Run(ctx, agent.NewClient(config), state, agent.NewWindowsState().Get)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: hospital-remote-agent install|uninstall|service|console")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "install":
		err = install(os.Args[2:])
	case "uninstall":
		err = uninstall()
	case "service":
		if len(os.Args) != 3 {
			err = errors.New("service config path is required")
		} else {
			err = svc.Run(serviceName, &serviceHandler{configPath: os.Args[2]})
		}
	case "console":
		if len(os.Args) != 3 {
			err = errors.New("config path is required")
		} else {
			err = runConsole(os.Args[2])
		}
	default:
		err = errors.New("unknown command")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
