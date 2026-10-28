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
	"golang.org/x/sys/windows/svc/eventlog"
	"golang.org/x/sys/windows/svc/mgr"
	"hospital/remote-agent/agent"
)

const serviceName = "HospitalRemoteAgent"

type serviceHandler struct{ configPath string }

func (handler *serviceHandler) Execute(_ []string, requests <-chan svc.ChangeRequest, statuses chan<- svc.Status) (bool, uint32) {
	statuses <- svc.Status{State: svc.StartPending}
	logger, _ := eventlog.Open(serviceName)
	if logger != nil {
		defer logger.Close()
	}
	config, err := agent.LoadConfig(handler.configPath)
	if err != nil {
		logServiceError(logger, "load protected configuration", err)
		return true, 1
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	windowsState := agent.NewWindowsState()
	client := agent.NewClient(config)
	runtimeState := agent.NewRuntimeState(config.UIToken)
	localFailures, err := startLocalControl(ctx, runtimeState.Handler(client))
	if err != nil {
		logServiceError(logger, "start local control", err)
		return true, 1
	}
	var tunnelFailures <-chan error
	if config.TunnelListen != "" {
		tunnelFailures, err = startTunnel(ctx, config)
		if err != nil {
			logServiceError(logger, "start secure tunnel", err)
			return true, 1
		}
	}
	agentFailures := make(chan error, 1)
	go func() { agentFailures <- agent.Run(ctx, client, runtimeState, windowsState.Get, vncReadiness(config)) }()
	statuses <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown | svc.AcceptSessionChange}
	for {
		select {
		case request, ok := <-requests:
			if !ok {
				cancel()
				return false, 0
			}
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
		case err = <-localFailures:
			logServiceError(logger, "local control stopped", err)
			statuses <- svc.Status{State: svc.StopPending}
			return true, 1
		case err = <-tunnelFailures:
			logServiceError(logger, "secure tunnel stopped", err)
			statuses <- svc.Status{State: svc.StopPending}
			return true, 1
		case err = <-agentFailures:
			logServiceError(logger, "agent loop stopped", err)
			statuses <- svc.Status{State: svc.StopPending}
			return true, 1
		}
	}
}

func logServiceError(logger *eventlog.Log, component string, err error) {
	if logger == nil {
		return
	}
	if err == nil {
		err = errors.New("stopped unexpectedly")
	}
	_ = logger.Error(1, component+": "+err.Error())
}

func startLocalControl(ctx context.Context, handler http.Handler) (<-chan error, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:19377")
	if err != nil {
		return nil, err
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 3 * time.Second}
	failures := make(chan error, 1)
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	go func() { failures <- server.Serve(listener) }()
	return failures, nil
}

func startTunnel(ctx context.Context, config agent.Config) (<-chan error, error) {
	if err := agent.ValidateTunnelConfig(config); err != nil {
		return nil, err
	}
	listener, err := net.Listen("tcp", config.TunnelListen)
	if err != nil {
		return nil, err
	}
	failures := make(chan error, 1)
	go func() { failures <- agent.ServeTunnel(ctx, listener, config) }()
	return failures, nil
}

func vncReadiness(config agent.Config) func(context.Context) error {
	return func(ctx context.Context) error {
		check, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		return agent.CheckLocalVNC(check, config.VNCAddress, config.VNCPassword)
	}
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
	tunnelListen := flags.String("tunnel-listen", "0.0.0.0:5910", "mutual-TLS tunnel listen address")
	vncAddress := flags.String("vnc-address", "127.0.0.1:5900", "loopback VNC server address")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *server == "" || *machine == "" || *uiUser == "" {
		return errors.New("--server, --machine, and --ui-user are required")
	}
	if !strings.HasPrefix(*server, "https://") && !strings.HasPrefix(*server, "http://localhost") && !strings.HasPrefix(*server, "http://127.0.0.1") {
		return errors.New("server must use HTTPS")
	}
	fmt.Fprint(os.Stderr, "One-time enrollment token: ")
	stdin := windows.Handle(os.Stdin.Fd())
	var consoleMode uint32
	consoleInput := windows.GetConsoleMode(stdin, &consoleMode) == nil
	if consoleInput {
		_ = windows.SetConsoleMode(stdin, consoleMode&^windows.ENABLE_ECHO_INPUT)
		defer windows.SetConsoleMode(stdin, consoleMode)
	}
	token, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if consoleInput {
		fmt.Fprintln(os.Stderr)
	}
	if err != nil && len(token) == 0 {
		return err
	}
	token = strings.TrimSpace(token)
	if len(token) < 32 {
		return errors.New("enrollment token is too short")
	}
	fmt.Fprint(os.Stderr, "Local VNC password (1-8 bytes): ")
	vncPassword, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if consoleInput {
		fmt.Fprintln(os.Stderr)
	}
	if err != nil && len(vncPassword) == 0 {
		return err
	}
	vncPassword = strings.TrimSpace(vncPassword)
	if len([]byte(vncPassword)) < 1 || len([]byte(vncPassword)) > 8 {
		return errors.New("VNC password must be 1 to 8 bytes")
	}
	certificate, privateKey, err := agent.GenerateMachineIdentity(*machine)
	if err != nil {
		return err
	}
	enrollment, err := agent.Enroll(*server, *machine, token, certificate)
	if err != nil {
		return err
	}
	directory := dataDirectory()
	if err := os.MkdirAll(directory, 0750); err != nil {
		return err
	}
	config := agent.Config{
		ServerURL: *server, MachineCode: *machine, AgentKey: enrollment.AgentKey, UIToken: randomToken(),
		TunnelListen: *tunnelListen, TunnelCertificate: certificate, TunnelPrivateKey: privateKey,
		GatewayCACertificate: enrollment.GatewayCACertificate, VNCAddress: *vncAddress, VNCPassword: vncPassword,
	}
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
	if err = eventlog.InstallAsEventCreate(serviceName, eventlog.Error|eventlog.Warning|eventlog.Info); err != nil && !errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		_ = service.Delete()
		return fmt.Errorf("register Windows Event Log source: %w", err)
	}
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
	if err = service.Delete(); err != nil {
		return err
	}
	_ = eventlog.Remove(serviceName)
	return nil
}

func runConsole(configPath string) error {
	config, err := agent.LoadConfig(configPath)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	state := agent.NewRuntimeState(config.UIToken)
	client := agent.NewClient(config)
	localFailures, err := startLocalControl(ctx, state.Handler(client))
	if err != nil {
		return err
	}
	var tunnelFailures <-chan error
	if config.TunnelListen != "" {
		tunnelFailures, err = startTunnel(ctx, config)
		if err != nil {
			return err
		}
	}
	agentFailures := make(chan error, 1)
	go func() {
		agentFailures <- agent.Run(ctx, client, state, agent.NewWindowsState().Get, vncReadiness(config))
	}()
	select {
	case err = <-localFailures:
	case err = <-tunnelFailures:
	case err = <-agentFailures:
	}
	return err
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
