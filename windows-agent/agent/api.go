package agent

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type Config struct {
	ServerURL             string `json:"server_url"`
	MachineCode           string `json:"machine_code"`
	AgentKey              string `json:"agent_key"`
	UIToken               string `json:"ui_token"`
	TunnelListen          string `json:"tunnel_listen,omitempty"`
	TunnelCertificate     string `json:"tunnel_certificate,omitempty"`
	TunnelPrivateKey      string `json:"tunnel_private_key,omitempty"`
	GatewayCACertificate  string `json:"gateway_ca_certificate,omitempty"`
	VNCAddress            string `json:"vnc_address,omitempty"`
	VNCPassword           string `json:"vnc_password,omitempty"`
	RemoteAccessMode      string `json:"remote_access_mode,omitempty"`
	AllowInsecureLoopback bool   `json:"allow_insecure_loopback,omitempty"`
}

type Session struct {
	ID             string `json:"id"`
	Status         string `json:"status"`
	MachineState   string `json:"machine_state"`
	DoctorUsername string `json:"doctor_username"`
	EndReason      string `json:"end_reason,omitempty"`
}

type Message struct {
	ID        int64     `json:"id"`
	Sender    string    `json:"sender"`
	Body      string    `json:"body"`
	CreatedAt time.Time `json:"created_at"`
}

type Client struct {
	base   *url.URL
	config Config
	http   *http.Client
}

type EnrollmentResult struct {
	MachineCode          string `json:"machine_code"`
	AgentKey             string `json:"agent_key"`
	GatewayCACertificate string `json:"gateway_ca_certificate"`
}

func Enroll(serverURL, machineCode, enrollmentToken, certificatePEM string) (EnrollmentResult, error) {
	var result EnrollmentResult
	base, err := url.Parse(strings.TrimRight(serverURL, "/"))
	if err != nil || base.Scheme == "" || base.Host == "" {
		return result, errors.New("invalid server URL")
	}
	input := map[string]string{
		"machine_code":          machineCode,
		"enrollment_token":      enrollmentToken,
		"agent_certificate_pem": certificatePEM,
	}
	body, err := json.Marshal(input)
	if err != nil {
		return result, err
	}
	reference, _ := url.Parse("/api/agent/enroll")
	request, err := http.NewRequest(http.MethodPost, base.ResolveReference(reference).String(), bytes.NewReader(body))
	if err != nil {
		return result, err
	}
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}}
	response, err := client.Do(request)
	if err != nil {
		return result, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return result, fmt.Errorf("enrollment failed with HTTP %d", response.StatusCode)
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return result, err
	}
	if len(result.AgentKey) < 32 {
		return result, errors.New("enrollment response is incomplete")
	}
	return result, nil
}

type enrollmentClaim struct {
	ID               string    `json:"claim_id"`
	VerificationCode string    `json:"verification_code"`
	ExpiresAt        time.Time `json:"expires_at"`
}

func EnrollWithApproval(serverURL, machineCode, certificatePEM string, showCode func(string)) (EnrollmentResult, error) {
	var result EnrollmentResult
	base, err := url.Parse(strings.TrimRight(serverURL, "/"))
	if err != nil || base.Scheme == "" || base.Host == "" {
		return result, errors.New("invalid server URL")
	}
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return result, err
	}
	publicDER, err := x509.MarshalPKIXPublicKey(&privateKey.PublicKey)
	if err != nil {
		return result, err
	}
	input := map[string]string{"machine_code": machineCode, "public_key_pem": string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: publicDER}))}
	client := &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}}
	requestJSON := func(path string, input any, output any) (int, error) {
		body, marshalErr := json.Marshal(input)
		if marshalErr != nil {
			return 0, marshalErr
		}
		reference, _ := url.Parse(path)
		request, requestErr := http.NewRequest(http.MethodPost, base.ResolveReference(reference).String(), bytes.NewReader(body))
		if requestErr != nil {
			return 0, requestErr
		}
		request.Header.Set("Content-Type", "application/json")
		response, requestErr := client.Do(request)
		if requestErr != nil {
			return 0, requestErr
		}
		defer response.Body.Close()
		if response.StatusCode >= 200 && response.StatusCode < 300 && output != nil {
			requestErr = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(output)
		}
		return response.StatusCode, requestErr
	}
	var claim enrollmentClaim
	status, err := requestJSON("/api/agent/enrollment-claims", input, &claim)
	if err != nil || status != http.StatusCreated {
		if err != nil {
			return result, fmt.Errorf("begin enrollment failed with HTTP %d: %w", status, err)
		}
		return result, fmt.Errorf("begin enrollment failed with HTTP %d", status)
	}
	if showCode != nil {
		showCode(claim.VerificationCode)
	}
	for time.Now().Before(claim.ExpiresAt) {
		var completed struct {
			Encrypted struct {
				Key        string `json:"key"`
				Nonce      string `json:"nonce"`
				Tag        string `json:"tag"`
				Ciphertext string `json:"ciphertext"`
			} `json:"encrypted_enrollment"`
		}
		status, err = requestJSON("/api/agent/enrollment-claims/"+url.PathEscape(claim.ID)+"/complete", map[string]string{"agent_certificate_pem": certificatePEM}, &completed)
		if err == nil && status == http.StatusCreated {
			encryptedKey, decodeErr := base64.StdEncoding.DecodeString(completed.Encrypted.Key)
			contentKey, decryptErr := rsa.DecryptOAEP(sha256.New(), rand.Reader, privateKey, encryptedKey, nil)
			nonce, nonceErr := base64.StdEncoding.DecodeString(completed.Encrypted.Nonce)
			tag, tagErr := base64.StdEncoding.DecodeString(completed.Encrypted.Tag)
			ciphertext, ciphertextErr := base64.StdEncoding.DecodeString(completed.Encrypted.Ciphertext)
			block, blockErr := aes.NewCipher(contentKey)
			if decodeErr != nil || decryptErr != nil || nonceErr != nil || tagErr != nil || ciphertextErr != nil || blockErr != nil {
				return result, errors.New("enrollment response could not be decrypted")
			}
			gcm, gcmErr := cipher.NewGCM(block)
			if gcmErr != nil {
				return result, errors.New("enrollment response could not be decrypted")
			}
			plaintext, decryptErr := gcm.Open(nil, nonce, append(ciphertext, tag...), nil)
			if decryptErr != nil {
				return result, errors.New("enrollment response could not be decrypted")
			}
			if json.Unmarshal(plaintext, &result) != nil || len(result.AgentKey) < 32 {
				return result, errors.New("enrollment response is incomplete")
			}
			return result, nil
		}
		if status != http.StatusConflict {
			if err != nil {
				return result, fmt.Errorf("complete enrollment failed with HTTP %d: %w", status, err)
			}
			return result, fmt.Errorf("complete enrollment failed with HTTP %d", status)
		}
		time.Sleep(2 * time.Second)
	}
	return result, errors.New("enrollment invitation expired before approval")
}

func NewClient(config Config) *Client {
	base, _ := url.Parse(strings.TrimRight(config.ServerURL, "/"))
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	return &Client{base: base, config: config, http: &http.Client{Timeout: 10 * time.Second, Transport: transport}}
}

func (c *Client) request(method, path string, input, output any) error {
	if c.base == nil || c.base.Scheme == "" || c.base.Host == "" {
		return errors.New("invalid server URL")
	}
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	reference, err := url.Parse(path)
	if err != nil {
		return err
	}
	request, err := http.NewRequest(method, c.base.ResolveReference(reference).String(), body)
	if err != nil {
		return err
	}
	request.Header.Set("X-Machine-Code", c.config.MachineCode)
	request.Header.Set("X-Agent-Key", c.config.AgentKey)
	if input != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNoContent {
		return io.EOF
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("server returned HTTP %d", response.StatusCode)
	}
	if output == nil {
		return nil
	}
	return json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(output)
}

func (c *Client) Heartbeat(state string, components ...map[string]ComponentHealth) error {
	input := map[string]any{"state": state}
	if len(components) > 0 {
		input["components"] = components[0]
	}
	return c.request(http.MethodPost, "/api/agent/heartbeat", input, nil)
}

func (c *Client) NextSession() (Session, error) {
	var session Session
	err := c.request(http.MethodGet, "/api/agent/remote-sessions/next", nil, &session)
	return session, err
}

func (c *Client) MarkReady(id string) (Session, error) {
	var session Session
	err := c.request(http.MethodPost, "/api/agent/remote-sessions/"+url.PathEscape(id)+"/ready", nil, &session)
	return session, err
}

func (c *Client) Messages(id string, after int64) ([]Message, error) {
	var messages []Message
	err := c.request(http.MethodGet, fmt.Sprintf("/api/agent/remote-sessions/%s/messages?after=%d", url.PathEscape(id), after), nil, &messages)
	return messages, err
}

func (c *Client) SendMessage(id, body string) (Message, error) {
	var message Message
	err := c.request(http.MethodPost, "/api/agent/remote-sessions/"+url.PathEscape(id)+"/messages", map[string]string{"body": body}, &message)
	return message, err
}

func (c *Client) End(id string) (Session, error) {
	var session Session
	err := c.request(http.MethodPost, "/api/agent/remote-sessions/"+url.PathEscape(id)+"/end", nil, &session)
	return session, err
}

func prepareRequestedSession(ctx context.Context, client *Client, session Session, readiness func(context.Context) error, authorizer *TunnelAuthorizer) (Session, error) {
	if session.Status != "requested" {
		return session, nil
	}
	if readiness == nil {
		return session, errors.New("remote-session readiness check is not configured")
	}
	if err := readiness(ctx); err != nil {
		return session, err
	}
	if authorizer == nil {
		return session, errors.New("tunnel authorization is not configured")
	}
	authorizer.Arm(session.ID)
	ready, err := client.MarkReady(session.ID)
	if err != nil {
		authorizer.Revoke(session.ID)
	}
	return ready, err
}

type Snapshot struct {
	MachineState string                     `json:"machine_state"`
	Session      *Session                   `json:"session,omitempty"`
	Messages     []Message                  `json:"messages"`
	Components   map[string]ComponentHealth `json:"components"`
}

type ComponentHealth struct {
	Status    string    `json:"status"`
	Error     string    `json:"error,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

func reportedComponents(snapshot Snapshot) map[string]ComponentHealth {
	reported := make(map[string]ComponentHealth, 2)
	for _, name := range []string{"tightvnc", "notification_ui"} {
		if health, ok := snapshot.Components[name]; ok {
			reported[name] = ComponentHealth{Status: health.Status, UpdatedAt: health.UpdatedAt}
		}
	}
	return reported
}

type RuntimeState struct {
	mu         sync.RWMutex
	uiToken    string
	authorizer *TunnelAuthorizer
	pendingEnd string
	persistEnd func(string) error
	snapshot   Snapshot
}

func NewRuntimeState(uiToken string, authorizer *TunnelAuthorizer) *RuntimeState {
	now := time.Now().UTC()
	return &RuntimeState{uiToken: uiToken, authorizer: authorizer, snapshot: Snapshot{
		MachineState: "booting", Messages: []Message{}, Components: map[string]ComponentHealth{
			"dashboard_connection": {Status: "starting", UpdatedAt: now},
			"secure_tunnel":        {Status: "starting", UpdatedAt: now},
			"tightvnc":             {Status: "unknown", UpdatedAt: now},
			"notification_ui":      {Status: "unknown", UpdatedAt: now},
		},
	}}
}

func (s *RuntimeState) SetMachineState(state string) {
	s.mu.Lock()
	s.snapshot.MachineState = state
	s.mu.Unlock()
}
func (s *RuntimeState) SetSession(session *Session) {
	s.mu.Lock()
	if session != nil && (s.snapshot.Session == nil || s.snapshot.Session.ID != session.ID) {
		s.snapshot.Messages = []Message{}
	}
	s.snapshot.Session = session
	if session == nil {
		s.snapshot.Messages = []Message{}
	}
	s.mu.Unlock()
}

func (s *RuntimeState) ClearSession(sessionID string) {
	s.mu.Lock()
	if s.snapshot.Session != nil && s.snapshot.Session.ID == sessionID {
		s.snapshot.Session = nil
		s.snapshot.Messages = []Message{}
	}
	s.mu.Unlock()
}

func (s *RuntimeState) setAuthorizedSession(session *Session, authorizer *TunnelAuthorizer) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if session == nil || s.pendingEnd != "" || authorizer == nil || !authorizer.Owns(session.ID) {
		return false
	}
	copy := *session
	s.snapshot.Session = &copy
	return true
}
func (s *RuntimeState) AddMessages(messages []Message) {
	s.mu.Lock()
	s.snapshot.Messages = append(s.snapshot.Messages, messages...)
	s.mu.Unlock()
}

func (s *RuntimeState) SetComponent(name, status, detail string) {
	s.mu.Lock()
	s.snapshot.Components[name] = ComponentHealth{Status: status, Error: detail, UpdatedAt: time.Now().UTC()}
	s.mu.Unlock()
}

func (s *RuntimeState) Snapshot() Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	copy := s.snapshot
	copy.Messages = append([]Message(nil), s.snapshot.Messages...)
	copy.Components = make(map[string]ComponentHealth, len(s.snapshot.Components))
	for name, health := range s.snapshot.Components {
		if name == "notification_ui" && health.Status == "healthy" && time.Since(health.UpdatedAt) > 15*time.Second {
			health.Status = "degraded"
			health.Error = "no notification UI heartbeat"
		}
		copy.Components[name] = health
	}
	return copy
}

func (s *RuntimeState) PendingEnd() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.pendingEnd
}

func (s *RuntimeState) SetPendingEndPersistence(pending string, persist func(string) error) {
	s.mu.Lock()
	s.pendingEnd = pending
	s.persistEnd = persist
	s.mu.Unlock()
}

func (s *RuntimeState) setPendingEnd(sessionID string) error {
	s.mu.RLock()
	persist := s.persistEnd
	s.mu.RUnlock()
	err := error(nil)
	if persist != nil {
		err = persist(sessionID)
	}
	s.mu.Lock()
	s.pendingEnd = sessionID
	s.mu.Unlock()
	return err
}

func (s *RuntimeState) clearPendingEnd(sessionID string) {
	s.mu.RLock()
	if s.pendingEnd != sessionID {
		s.mu.RUnlock()
		return
	}
	persist := s.persistEnd
	s.mu.RUnlock()
	if persist != nil && persist("") != nil {
		return
	}
	s.mu.Lock()
	if s.pendingEnd == sessionID {
		s.pendingEnd = ""
	}
	s.mu.Unlock()
}

func (s *RuntimeState) checkSessionReadiness(ctx context.Context, readiness func(context.Context) error) error {
	s.mu.RLock()
	tunnel := s.snapshot.Components["secure_tunnel"]
	s.mu.RUnlock()
	if tunnel.Status != "healthy" {
		return errors.New("secure tunnel is not healthy")
	}
	return readiness(ctx)
}

func (s *RuntimeState) Handler(client *Client) http.Handler {
	mux := http.NewServeMux()
	authorized := func(handler http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			supplied := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			if len(supplied) != len(s.uiToken) || subtle.ConstantTimeCompare([]byte(supplied), []byte(s.uiToken)) != 1 {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			s.SetComponent("notification_ui", "healthy", "")
			handler(w, r)
		}
	}
	method := func(expected string, handler http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if r.Method != expected {
				w.Header().Set("Allow", expected)
				http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			handler(w, r)
		}
	}
	mux.HandleFunc("/state", method(http.MethodGet, authorized(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.Snapshot())
	})))
	mux.HandleFunc("/end", method(http.MethodPost, authorized(func(w http.ResponseWriter, _ *http.Request) {
		s.mu.Lock()
		session := s.snapshot.Session
		if session == nil {
			s.mu.Unlock()
			http.Error(w, "no active session", http.StatusConflict)
			return
		}
		s.snapshot.Session = nil
		s.snapshot.Messages = []Message{}
		s.mu.Unlock()
		if s.authorizer != nil {
			s.authorizer.Revoke(session.ID)
		}
		if err := s.setPendingEnd(session.ID); err != nil {
			http.Error(w, "unable to persist end request", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})))
	mux.HandleFunc("/messages", method(http.MethodPost, authorized(func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		session := s.snapshot.Session
		s.mu.RUnlock()
		if client == nil || session == nil {
			http.Error(w, "no active session", http.StatusConflict)
			return
		}
		var input struct {
			Body string `json:"body"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 2048)).Decode(&input); err != nil || len(strings.TrimSpace(input.Body)) == 0 || len(input.Body) > 500 {
			http.Error(w, "invalid message", http.StatusUnprocessableEntity)
			return
		}
		if _, err := client.SendMessage(session.ID, strings.TrimSpace(input.Body)); err != nil {
			http.Error(w, "unable to send message", http.StatusBadGateway)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})))
	return mux
}

func Run(ctx context.Context, client *Client, state *RuntimeState, machineState func() string, readiness func(context.Context) error, authorizer *TunnelAuthorizer) error {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	var active *Session
	var lastMessage int64
	lastVNCCheck := time.Time{}
	checkVNC := func(ctx context.Context) error {
		if readiness == nil {
			state.SetComponent("tightvnc", "degraded", "readiness check is not configured")
			return errors.New("remote-session readiness check is not configured")
		}
		err := readiness(ctx)
		if err != nil {
			state.SetComponent("tightvnc", "degraded", err.Error())
			return err
		}
		state.SetComponent("tightvnc", "healthy", "")
		return nil
	}
	checkSession := func(ctx context.Context) error {
		return state.checkSessionReadiness(ctx, checkVNC)
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
		current := machineState()
		state.SetMachineState(current)
		if time.Since(lastVNCCheck) >= 30*time.Second {
			_ = checkVNC(ctx)
			lastVNCCheck = time.Now()
		}
		if pending := state.PendingEnd(); pending != "" {
			if _, err := client.End(pending); err == nil {
				state.clearPendingEnd(pending)
			}
		}
		if err := client.Heartbeat(current, reportedComponents(state.Snapshot())); err != nil {
			state.SetComponent("dashboard_connection", "degraded", err.Error())
			continue
		}
		state.SetComponent("dashboard_connection", "healthy", "")
		session, err := client.NextSession()
		if errors.Is(err, io.EOF) {
			if active != nil && authorizer != nil {
				authorizer.Revoke(active.ID)
			}
			active = nil
			lastMessage = 0
			state.SetSession(nil)
			continue
		}
		if err != nil {
			state.SetComponent("dashboard_connection", "degraded", err.Error())
			continue
		}
		if session.Status != "requested" && (authorizer == nil || !authorizer.Owns(session.ID)) {
			_, _ = client.End(session.ID)
			state.SetSession(nil)
			continue
		}
		session, err = prepareRequestedSession(ctx, client, session, checkSession, authorizer)
		if err != nil {
			continue
		}
		if !state.setAuthorizedSession(&session, authorizer) {
			active = nil
			continue
		}
		active = &session
		messages, err := client.Messages(active.ID, lastMessage)
		if err == nil && len(messages) > 0 {
			lastMessage = messages[len(messages)-1].ID
			state.AddMessages(messages)
		}
	}
}
