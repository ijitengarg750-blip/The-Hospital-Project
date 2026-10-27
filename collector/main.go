package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type Config struct {
	MachineCode    string   `json:"machine_code"`
	ServerURL      string   `json:"server_url"`
	CollectorKey   string   `json:"collector_key"`
	Folders        []string `json:"watch_folders"`
	Extensions     []string `json:"extensions"`
	PractoUIDRegex string   `json:"practo_uid_regex"`
	StudyUIDRegex  string   `json:"study_uid_regex"`
	StudyTitle     string   `json:"study_title"`
	PollSeconds    int      `json:"poll_seconds"`
	StableChecks   int      `json:"stable_checks"`
	StateFile      string   `json:"state_file"`
}

type Failure struct {
	Digest      string    `json:"digest"`
	Attempts    int       `json:"attempts"`
	NextAttempt time.Time `json:"next_attempt"`
	Permanent   bool      `json:"permanent"`
	LastError   string    `json:"last_error"`
}

type State struct {
	Uploaded map[string]string  `json:"uploaded"`
	Failures map[string]Failure `json:"failures,omitempty"`
}

type uploadError struct {
	StatusCode int
	Detail     string
}

func (e *uploadError) Error() string {
	return fmt.Sprintf("server returned HTTP %d: %s", e.StatusCode, e.Detail)
}

func isPermanentFailure(err error) bool {
	var responseError *uploadError
	if !errors.As(err, &responseError) {
		return false
	}
	return responseError.StatusCode >= 400 && responseError.StatusCode < 500 && responseError.StatusCode != http.StatusUnauthorized && responseError.StatusCode != http.StatusForbidden && responseError.StatusCode != http.StatusRequestTimeout && responseError.StatusCode != http.StatusTooManyRequests
}

func retryDelay(attempts int) time.Duration {
	if attempts < 1 {
		attempts = 1
	}
	delay := 15 * time.Second
	for i := 1; i < attempts && delay < 15*time.Minute; i++ {
		delay *= 2
	}
	if delay > 15*time.Minute {
		return 15 * time.Minute
	}
	return delay
}

func validateServerURL(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return errors.New("server_url must be an absolute HTTPS URL")
	}
	if parsed.Scheme == "https" {
		return nil
	}
	if parsed.Scheme == "http" && (parsed.Hostname() == "localhost" || parsed.Hostname() == "127.0.0.1" || parsed.Hostname() == "::1") {
		return nil
	}
	return errors.New("server_url must use HTTPS except for loopback development")
}

func migrateCredentialFailures(state *State) {
	for key, failure := range state.Failures {
		if failure.Permanent && (strings.Contains(failure.LastError, "HTTP 401") || strings.Contains(failure.LastError, "HTTP 403")) {
			failure.Permanent = false
			failure.NextAttempt = time.Time{}
			state.Failures[key] = failure
		}
	}
}

func (s *State) shouldAttempt(key, digest string, now time.Time) bool {
	failure, exists := s.Failures[key]
	if !exists {
		return true
	}
	if failure.Digest != digest {
		delete(s.Failures, key)
		return true
	}
	return !failure.Permanent && !now.Before(failure.NextAttempt)
}

func (s *State) recordFailure(key, digest string, err error, now time.Time) {
	if s.Failures == nil {
		s.Failures = map[string]Failure{}
	}
	failure := s.Failures[key]
	if failure.Digest != digest {
		failure = Failure{Digest: digest}
	}
	failure.Attempts++
	failure.Permanent = isPermanentFailure(err)
	failure.LastError = err.Error()
	if failure.Permanent {
		failure.NextAttempt = time.Time{}
	} else {
		failure.NextAttempt = now.Add(retryDelay(failure.Attempts))
	}
	s.Failures[key] = failure
}

func loadJSON(path string, target interface{}) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}

func saveState(path string, state State) error {
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	temp := path + ".tmp"
	if err = os.WriteFile(temp, data, 0600); err != nil {
		return err
	}
	return os.Rename(temp, path)
}

func checksum(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func isStable(path string, checks int) bool {
	if checks < 2 {
		checks = 2
	}
	var last os.FileInfo
	for i := 0; i < checks; i++ {
		info, err := os.Stat(path)
		if err != nil || info.Size() == 0 {
			return false
		}
		if last != nil && (info.Size() != last.Size() || !info.ModTime().Equal(last.ModTime())) {
			return false
		}
		last = info
		if i < checks-1 {
			time.Sleep(2 * time.Second)
		}
	}
	return true
}

func upload(cfg Config, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	_ = w.WriteField("machine_code", cfg.MachineCode)
	if cfg.StudyTitle != "" {
		_ = w.WriteField("title", cfg.StudyTitle)
	}
	if cfg.PractoUIDRegex != "" {
		pattern, e := regexp.Compile(cfg.PractoUIDRegex)
		if e != nil {
			return fmt.Errorf("invalid practo_uid_regex: %v", e)
		}
		match := pattern.FindStringSubmatch(path)
		if len(match) > 1 {
			_ = w.WriteField("practo_uid", match[1])
		} else if len(match) == 1 {
			_ = w.WriteField("practo_uid", match[0])
		}
	}
	if cfg.StudyUIDRegex != "" {
		pattern, e := regexp.Compile(cfg.StudyUIDRegex)
		if e != nil {
			return fmt.Errorf("invalid study_uid_regex: %v", e)
		}
		match := pattern.FindStringSubmatch(path)
		if len(match) > 1 {
			_ = w.WriteField("study_uid", cfg.MachineCode+":"+match[1])
		} else if len(match) == 1 {
			_ = w.WriteField("study_uid", cfg.MachineCode+":"+match[0])
		}
	}
	part, err := w.CreateFormFile("file", filepath.Base(path))
	if err != nil {
		return err
	}
	if _, err = io.Copy(part, f); err != nil {
		return err
	}
	if err = w.Close(); err != nil {
		return err
	}
	req, err := http.NewRequest("POST", strings.TrimRight(cfg.ServerURL, "/")+"/api/collector/upload", &body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())
	req.Header.Set("X-Machine-Code", cfg.MachineCode)
	req.Header.Set("X-Collector-Key", cfg.CollectorKey)
	client := &http.Client{
		Timeout:       30 * time.Minute,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return &uploadError{StatusCode: resp.StatusCode, Detail: string(detail)}
	}
	return nil
}

func allowed(path string, extensions []string) bool {
	ext := strings.ToLower(filepath.Ext(path))
	for _, candidate := range extensions {
		if ext == strings.ToLower(candidate) {
			return true
		}
	}
	return false
}

func scan(cfg Config, state *State) {
	for _, root := range cfg.Folders {
		info, err := os.Stat(root)
		if err != nil {
			log.Printf("event=watch_folder_unavailable folder=%q error=%q", root, err)
			continue
		}
		if !info.IsDir() {
			log.Printf("event=watch_path_not_directory path=%q", root)
			continue
		}
		_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				log.Printf("cannot access %s: %v", path, err)
				return nil
			}
			if info == nil || info.IsDir() || !allowed(path, cfg.Extensions) {
				return nil
			}
			key := strings.ToLower(filepath.Clean(path))
			if old, ok := state.Uploaded[key]; ok {
				digest, e := checksum(path)
				if e == nil && digest == old {
					return nil
				}
			}
			if !isStable(path, cfg.StableChecks) {
				log.Printf("waiting for stable file: %s", path)
				return nil
			}
			digest, e := checksum(path)
			if e != nil {
				log.Printf("checksum failed for %s: %v", path, e)
				return nil
			}
			if !state.shouldAttempt(key, digest, time.Now()) {
				log.Printf("event=upload_deferred file=%q reason=retry_backoff_or_permanent_failure", path)
				return nil
			}
			if e = upload(cfg, path); e != nil {
				state.recordFailure(key, digest, e, time.Now())
				if saveErr := saveState(cfg.StateFile, *state); saveErr != nil {
					log.Printf("event=state_save_failed file=%q error=%q", path, saveErr)
				}
				failure := state.Failures[key]
				status := "temporary_failure"
				if failure.Permanent {
					status = "permanent_failure"
				}
				log.Printf("event=upload_%s file=%q attempts=%d error=%q", status, path, failure.Attempts, e)
				return nil
			}
			state.Uploaded[key] = digest
			delete(state.Failures, key)
			if e = saveState(cfg.StateFile, *state); e != nil {
				log.Printf("event=state_save_failed file=%q error=%q", path, e)
			}
			log.Printf("event=upload_accepted file=%q", path)
			return nil
		})
	}
}

func main() {
	configPath := "config.json"
	if len(os.Args) > 1 {
		configPath = os.Args[1]
	}
	var cfg Config
	if err := loadJSON(configPath, &cfg); err != nil {
		log.Fatalf("cannot load config: %v", err)
	}
	if cfg.MachineCode == "" || cfg.ServerURL == "" || cfg.CollectorKey == "" || len(cfg.Folders) == 0 {
		log.Fatal("machine_code, server_url, collector_key and watch_folders are required")
	}
	if err := validateServerURL(cfg.ServerURL); err != nil {
		log.Fatal(err)
	}
	if cfg.StateFile == "" {
		cfg.StateFile = "state.json"
	}
	configDir, err := filepath.Abs(filepath.Dir(configPath))
	if err != nil {
		log.Fatalf("cannot resolve config directory: %v", err)
	}
	if !filepath.IsAbs(cfg.StateFile) {
		cfg.StateFile = filepath.Join(configDir, cfg.StateFile)
	}
	for _, root := range cfg.Folders {
		info, err := os.Stat(root)
		if err != nil {
			log.Printf("WARNING: watch folder unavailable: %s (%v)", root, err)
		} else if !info.IsDir() {
			log.Printf("WARNING: watch path is not a directory: %s", root)
		} else {
			log.Printf("watching folder: %s", root)
		}
	}
	if cfg.PollSeconds < 5 {
		cfg.PollSeconds = 10
	}
	if cfg.StableChecks < 2 {
		cfg.StableChecks = 3
	}
	state := State{Uploaded: map[string]string{}, Failures: map[string]Failure{}}
	if err := loadJSON(cfg.StateFile, &state); err != nil && !os.IsNotExist(err) {
		log.Printf("state file could not be read; starting safely: %v", err)
	}
	if state.Uploaded == nil {
		state.Uploaded = map[string]string{}
	}
	if state.Failures == nil {
		state.Failures = map[string]Failure{}
	}
	migrateCredentialFailures(&state)
	log.Printf("collector started for %s -> %s", cfg.MachineCode, strings.TrimRight(cfg.ServerURL, "/"))
	for {
		scan(cfg, &state)
		time.Sleep(time.Duration(cfg.PollSeconds) * time.Second)
	}
}
