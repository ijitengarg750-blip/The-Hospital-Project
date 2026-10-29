package agent

import (
	"encoding/json"
	"errors"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

func protect(data []byte) ([]byte, error) {
	if len(data) == 0 {
		return nil, errors.New("cannot protect empty data")
	}
	in := windows.DataBlob{Size: uint32(len(data)), Data: &data[0]}
	var out windows.DataBlob
	err := windows.CryptProtectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_LOCAL_MACHINE|windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	if err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}

func unprotect(data []byte) ([]byte, error) {
	if len(data) == 0 {
		return nil, errors.New("protected data is empty")
	}
	in := windows.DataBlob{Size: uint32(len(data)), Data: &data[0]}
	var out windows.DataBlob
	err := windows.CryptUnprotectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	if err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}

func SaveConfig(path string, config Config) error {
	plain, err := json.Marshal(config)
	if err != nil {
		return err
	}
	protected, err := protect(plain)
	for index := range plain {
		plain[index] = 0
	}
	if err != nil {
		return err
	}
	return os.WriteFile(path, protected, 0600)
}

func LoadConfig(path string) (Config, error) {
	var config Config
	protected, err := os.ReadFile(path)
	if err != nil {
		return config, err
	}
	plain, err := unprotect(protected)
	if err != nil {
		return config, err
	}
	defer func() {
		for index := range plain {
			plain[index] = 0
		}
	}()
	err = json.Unmarshal(plain, &config)
	return config, err
}

func SavePendingEnd(path, sessionID string) error {
	plain, err := json.Marshal(struct {
		SessionID string `json:"session_id"`
	}{SessionID: sessionID})
	if err != nil {
		return err
	}
	protected, err := protect(plain)
	for index := range plain {
		plain[index] = 0
	}
	if err != nil {
		return err
	}
	return os.WriteFile(path, protected, 0600)
}

func LoadPendingEnd(path string) (string, error) {
	protected, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	plain, err := unprotect(protected)
	if err != nil {
		return "", err
	}
	defer func() {
		for index := range plain {
			plain[index] = 0
		}
	}()
	var value struct {
		SessionID string `json:"session_id"`
	}
	if err = json.Unmarshal(plain, &value); err != nil {
		return "", err
	}
	return value.SessionID, nil
}

type WindowsCredential struct {
	Username string `json:"username"`
	Domain   string `json:"domain"`
	Password string `json:"password"`
}

func SaveWindowsCredential(path string, credential WindowsCredential) error {
	plain, err := json.Marshal(credential)
	if err != nil {
		return err
	}
	protected, err := protect(plain)
	for index := range plain {
		plain[index] = 0
	}
	if err != nil {
		return err
	}
	return os.WriteFile(path, protected, 0600)
}

func LoadWindowsCredential(path string) (WindowsCredential, error) {
	var credential WindowsCredential
	protected, err := os.ReadFile(path)
	if err != nil {
		return credential, err
	}
	plain, err := unprotect(protected)
	if err != nil {
		return credential, err
	}
	defer func() {
		for index := range plain {
			plain[index] = 0
		}
	}()
	err = json.Unmarshal(plain, &credential)
	return credential, err
}
