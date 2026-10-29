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
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/eventlog"
	"golang.org/x/sys/windows/svc/mgr"
	"hospital/remote-agent/agent"
)

const serviceName = "HospitalRemoteAgent"

type serviceHandler struct{ configPath string }

type componentEvent struct {
	name   string
	status string
	err    error
}

func componentFailure(err error) <-chan error {
	failures := make(chan error, 1)
	failures <- err
	return failures
}

func superviseComponent(ctx context.Context, name string, failures <-chan error, start func() (<-chan error, error), retryDelay time.Duration, events chan<- componentEvent) {
	for {
		select {
		case <-ctx.Done():
			return
		case err := <-failures:
			if err == nil {
				err = errors.New("stopped unexpectedly")
			}
			select {
			case events <- componentEvent{name: name, status: "degraded", err: err}:
			case <-ctx.Done():
				return
			}
		}
		for {
			timer := time.NewTimer(retryDelay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
			var err error
			failures, err = start()
			if err != nil {
				select {
				case events <- componentEvent{name: name, status: "degraded", err: err}:
				case <-ctx.Done():
					return
				}
				continue
			}
			select {
			case events <- componentEvent{name: name, status: "healthy"}:
			case <-ctx.Done():
				return
			}
			break
		}
	}
}

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
	var authorizer *agent.TunnelAuthorizer
	if config.RemoteAccessMode == "inbound_lab" {
		authorizer = agent.NewTunnelAuthorizer(config.AgentKey)
	}
	runtimeState := agent.NewRuntimeState(config.UIToken, authorizer)
	if err = configurePendingEndPersistence(runtimeState, handler.configPath+".pending"); err != nil {
		logServiceError(logger, "load pending end state", err)
		return true, 1
	}
	localFailures, err := startLocalControl(ctx, runtimeState.Handler(client))
	localHealthy := err == nil
	if !localHealthy {
		localFailures = componentFailure(err)
		runtimeState.SetComponent("local_control", "degraded", err.Error())
	}
	var tunnelFailures <-chan error
	tunnelHealthy := false
	if config.RemoteAccessMode == "inbound_lab" && config.TunnelListen != "" {
		tunnelFailures, err = startTunnel(ctx, config, authorizer)
		tunnelHealthy = err == nil
		if !tunnelHealthy {
			tunnelFailures = componentFailure(err)
			runtimeState.SetComponent("secure_tunnel", "degraded", err.Error())
		}
	}
	agentFailures := make(chan error, 1)
	go func() {
		agentFailures <- runRemoteConnector(ctx, config, client, runtimeState, windowsState.Get, authorizer)
	}()
	componentEvents := make(chan componentEvent, 8)
	runtimeState.SetComponent("agent_loop", "healthy", "")
	if localHealthy {
		runtimeState.SetComponent("local_control", "healthy", "")
	}
	go superviseComponent(ctx, "local_control", localFailures, func() (<-chan error, error) {
		return startLocalControl(ctx, runtimeState.Handler(client))
	}, 2*time.Second, componentEvents)
	if tunnelFailures != nil {
		if tunnelHealthy {
			runtimeState.SetComponent("secure_tunnel", "healthy", "")
		}
		go superviseComponent(ctx, "secure_tunnel", tunnelFailures, func() (<-chan error, error) {
			return startTunnel(ctx, config, authorizer)
		}, 2*time.Second, componentEvents)
	} else {
		runtimeState.SetComponent("secure_tunnel", "disabled", "")
	}
	go superviseComponent(ctx, "agent_loop", agentFailures, func() (<-chan error, error) {
		failures := make(chan error, 1)
		go func() {
			failures <- runRemoteConnector(ctx, config, client, runtimeState, windowsState.Get, authorizer)
		}()
		return failures, nil
	}, 2*time.Second, componentEvents)
	healthTicker := time.NewTicker(15 * time.Second)
	defer healthTicker.Stop()
	lastHealth := make(map[string]string)
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
		case event := <-componentEvents:
			detail := ""
			if event.err != nil {
				detail = event.err.Error()
			}
			runtimeState.SetComponent(event.name, event.status, detail)
			if logger != nil {
				if event.status == "healthy" {
					_ = logger.Info(2, event.name+": recovered")
				} else {
					_ = logger.Warning(2, event.name+": "+detail)
				}
			}
		case <-healthTicker.C:
			for name, health := range runtimeState.Snapshot().Components {
				previous := lastHealth[name]
				if health.Status == previous {
					continue
				}
				lastHealth[name] = health.Status
				if logger == nil {
					continue
				}
				if health.Status == "degraded" {
					_ = logger.Warning(3, name+": "+health.Error)
				} else if health.Status == "healthy" && previous == "degraded" {
					_ = logger.Info(3, name+": recovered")
				}
			}
		}
	}
}

func runRemoteConnector(ctx context.Context, config agent.Config, client *agent.Client, state *agent.RuntimeState, machineState func() string, authorizer *agent.TunnelAuthorizer) error {
	switch config.RemoteAccessMode {
	case "outbound":
		state.SetComponent("secure_tunnel", "disabled", "")
		return agent.RunOutbound(ctx, config, state, machineState)
	case "inbound_lab":
		return agent.Run(ctx, client, state, machineState, vncReadiness(config), authorizer)
	default:
		return errors.New("remote_access_mode must be outbound or inbound_lab")
	}
}

func configurePendingEndPersistence(state *agent.RuntimeState, path string) error {
	pending, err := agent.LoadPendingEnd(path)
	if err != nil {
		return err
	}
	var mu sync.Mutex
	state.SetPendingEndPersistence(pending, func(sessionID string) error {
		mu.Lock()
		defer mu.Unlock()
		return agent.SavePendingEnd(path, sessionID)
	})
	return nil
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

func startTunnel(ctx context.Context, config agent.Config, authorizer *agent.TunnelAuthorizer) (<-chan error, error) {
	if err := agent.ValidateTunnelConfig(config); err != nil {
		return nil, err
	}
	listener, err := net.Listen("tcp", config.TunnelListen)
	if err != nil {
		return nil, err
	}
	failures := make(chan error, 1)
	go func() { failures <- agent.ServeTunnel(ctx, listener, config, authorizer) }()
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
	tunnelListen := flags.String("tunnel-listen", "", "lab-only mutual-TLS tunnel listen address")
	remoteMode := flags.String("remote-mode", "outbound", "remote transport: outbound or inbound_lab")
	vncAddress := flags.String("vnc-address", "127.0.0.1:5900", "loopback VNC server address")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *server == "" || *machine == "" || *uiUser == "" {
		return errors.New("--server, --machine, and --ui-user are required")
	}
	if *remoteMode != "outbound" && *remoteMode != "inbound_lab" {
		return errors.New("--remote-mode must be outbound or inbound_lab")
	}
	if *remoteMode == "inbound_lab" && *tunnelListen == "" {
		return errors.New("--tunnel-listen is required for inbound_lab mode")
	}
	if *remoteMode == "outbound" {
		*tunnelListen = ""
		if err := agent.ValidateOutboundServerURL(*server); err != nil {
			return err
		}
	}
	if !strings.HasPrefix(*server, "https://") && !strings.HasPrefix(*server, "http://localhost") && !strings.HasPrefix(*server, "http://127.0.0.1") {
		return errors.New("server must use HTTPS")
	}
	stdin := windows.Handle(os.Stdin.Fd())
	var consoleMode uint32
	consoleInput := windows.GetConsoleMode(stdin, &consoleMode) == nil
	if consoleInput {
		_ = windows.SetConsoleMode(stdin, consoleMode&^windows.ENABLE_ECHO_INPUT)
		defer windows.SetConsoleMode(stdin, consoleMode)
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
	certificate, privateKey := "", ""
	if *remoteMode == "inbound_lab" {
		certificate, privateKey, err = agent.GenerateMachineIdentity(*machine)
		if err != nil {
			return err
		}
	}
	enrollment, err := agent.EnrollWithApproval(*server, *machine, certificate, func(code string) {
		fmt.Fprintf(os.Stderr, "Approve this diagnostic PC in the dashboard using verification code %s. Waiting for approval...\n", code)
	})
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
		RemoteAccessMode: *remoteMode,
	}
	serviceConfig := filepath.Join(directory, "service.dat")
	pendingEnd := serviceConfig + ".pending"
	uiConfig := filepath.Join(directory, "ui.dat")
	if err := agent.SaveConfig(serviceConfig, config); err != nil {
		return err
	}
	if err := agent.SavePendingEnd(pendingEnd, ""); err != nil {
		return err
	}
	if err := agent.SaveConfig(uiConfig, agent.Config{UIToken: config.UIToken}); err != nil {
		return err
	}
	if err := exec.Command("icacls", serviceConfig, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F").Run(); err != nil {
		return fmt.Errorf("secure service config ACL: %w", err)
	}
	if err := exec.Command("icacls", pendingEnd, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F").Run(); err != nil {
		return fmt.Errorf("secure pending end ACL: %w", err)
	}
	if err := exec.Command("icacls", uiConfig, "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F", *uiUser+":R").Run(); err != nil {
		return fmt.Errorf("secure UI config ACL: %w", err)
	}
	return ensureService(serviceConfig)
}

func serviceBinaryPath(executable, configPath string) string {
	return strings.Join([]string{syscall.EscapeArg(executable), "service", syscall.EscapeArg(configPath)}, " ")
}

func ensureService(configPath string) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	service, err := manager.OpenService(serviceName)
	created := false
	if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
		service, err = manager.CreateService(serviceName, executable, mgr.Config{DisplayName: "Hospital Remote Diagnostic Agent", Description: "Authorizes and controls audited diagnostic remote sessions.", StartType: mgr.StartAutomatic}, "service", configPath)
		created = err == nil
	} else if err == nil {
		if stopErr := stopService(service); stopErr != nil {
			service.Close()
			return stopErr
		}
		config, configErr := service.Config()
		if configErr != nil {
			service.Close()
			return configErr
		}
		config.BinaryPathName = serviceBinaryPath(executable, configPath)
		config.DisplayName = "Hospital Remote Diagnostic Agent"
		config.Description = "Authorizes and controls audited diagnostic remote sessions."
		config.StartType = mgr.StartAutomatic
		config.ServiceStartName = "LocalSystem"
		config.Password = ""
		err = service.UpdateConfig(config)
	}
	if err != nil {
		return err
	}
	defer service.Close()
	if err = eventlog.InstallAsEventCreate(serviceName, eventlog.Error|eventlog.Warning|eventlog.Info); err != nil && !errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		if created {
			_ = service.Delete()
		}
		return fmt.Errorf("register Windows Event Log source: %w", err)
	}
	err = service.Start()
	if err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return err
	}
	if output, failureErr := exec.Command("sc.exe", "failure", serviceName, "reset=", "86400", "actions=", "restart/5000/restart/15000/restart/30000").CombinedOutput(); failureErr != nil {
		return fmt.Errorf("configure service recovery: %w: %s", failureErr, strings.TrimSpace(string(output)))
	}
	return nil
}

func stopService(service *mgr.Service) error {
	status, err := service.Query()
	if err != nil {
		return err
	}
	if status.State == svc.Stopped {
		return nil
	}
	if _, err = service.Control(svc.Stop); err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
		return fmt.Errorf("stop service for repair: %w", err)
	}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		status, err = service.Query()
		if err != nil {
			return err
		}
		if status.State == svc.Stopped {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return errors.New("service did not stop within 30 seconds")
}

func repairService(configPath string) error {
	if _, err := agent.LoadConfig(configPath); err != nil {
		return fmt.Errorf("load existing enrollment: %w", err)
	}
	return ensureService(configPath)
}

func repairServiceIfConfigured(configPath string) error {
	if _, err := os.Stat(configPath); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	return repairService(configPath)
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
	var authorizer *agent.TunnelAuthorizer
	if config.RemoteAccessMode == "inbound_lab" {
		authorizer = agent.NewTunnelAuthorizer(config.AgentKey)
	}
	state := agent.NewRuntimeState(config.UIToken, authorizer)
	if err = configurePendingEndPersistence(state, configPath+".pending"); err != nil {
		return err
	}
	client := agent.NewClient(config)
	localFailures, err := startLocalControl(ctx, state.Handler(client))
	if err != nil {
		return err
	}
	state.SetComponent("local_control", "healthy", "")
	var tunnelFailures <-chan error
	if config.RemoteAccessMode == "inbound_lab" && config.TunnelListen != "" {
		tunnelFailures, err = startTunnel(ctx, config, authorizer)
		if err != nil {
			return err
		}
		state.SetComponent("secure_tunnel", "healthy", "")
	} else {
		state.SetComponent("secure_tunnel", "disabled", "")
	}
	state.SetComponent("agent_loop", "healthy", "")
	agentFailures := make(chan error, 1)
	go func() {
		agentFailures <- runRemoteConnector(ctx, config, client, state, agent.NewWindowsState().Get, authorizer)
	}()
	select {
	case err = <-localFailures:
	case err = <-tunnelFailures:
	case err = <-agentFailures:
	}
	return err
}

func run(args []string) error {
	if len(args) < 1 {
		return errors.New("usage: hospital-remote-agent install|repair-service|repair-service-if-configured|uninstall|service|console|preflight")
	}
	var err error
	switch args[0] {
	case "install":
		err = install(args[1:])
	case "uninstall":
		err = uninstall()
	case "repair-service":
		if len(args) != 2 {
			err = errors.New("repair-service requires the existing config path")
		} else {
			err = repairService(args[1])
		}
	case "repair-service-if-configured":
		if len(args) != 2 {
			err = errors.New("repair-service-if-configured requires the expected config path")
		} else {
			err = repairServiceIfConfigured(args[1])
		}
	case "service":
		if len(args) != 2 {
			err = errors.New("service config path is required")
		} else {
			err = svc.Run(serviceName, &serviceHandler{configPath: args[1]})
		}
	case "console":
		if len(args) != 2 {
			err = errors.New("config path is required")
		} else {
			err = runConsole(args[1])
		}
	case "preflight":
		if len(args) != 1 {
			err = errors.New("preflight does not accept arguments")
		}
	default:
		err = errors.New("unknown command")
	}
	return err
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: hospital-remote-agent install|repair-service|repair-service-if-configured|uninstall|service|console|preflight")
		os.Exit(2)
	}
	err := run(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
