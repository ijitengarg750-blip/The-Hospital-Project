//go:build windows

package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

func readCollectorKey() (string, error) {
	stdin := windows.Handle(os.Stdin.Fd())
	var mode uint32
	console := windows.GetConsoleMode(stdin, &mode) == nil
	if console {
		_ = windows.SetConsoleMode(stdin, mode&^windows.ENABLE_ECHO_INPUT)
		defer windows.SetConsoleMode(stdin, mode)
	}
	fmt.Fprint(os.Stderr, "Per-machine collector credential: ")
	value, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if console {
		fmt.Fprintln(os.Stderr)
	}
	if err != nil && value == "" {
		return "", err
	}
	value = strings.TrimSpace(value)
	if len(value) < 32 {
		return "", errors.New("collector credential is too short")
	}
	return value, nil
}

func restrictCollectorKey(path, serviceUser string) error {
	if strings.TrimSpace(serviceUser) == "" {
		return errors.New("collector service account is required")
	}
	sid, _, _, err := windows.LookupSID("", serviceUser)
	if err != nil {
		return fmt.Errorf("resolve collector service account: %w", err)
	}
	descriptor, err := windows.SecurityDescriptorFromString(fmt.Sprintf("D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;%s)", sid.String()))
	if err != nil {
		return fmt.Errorf("build collector credential ACL: %w", err)
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return fmt.Errorf("read collector credential ACL: %w", err)
	}
	if err = windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil); err != nil {
		return fmt.Errorf("restrict collector credential ACL: %w", err)
	}
	return nil
}

func createCollectorCredential(path, key, serviceUser string) error {
	if _, err := os.Lstat(path); err == nil {
		return os.ErrExist
	} else if !os.IsNotExist(err) {
		return err
	}
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return err
	}
	temporary := path + "." + hex.EncodeToString(random) + ".tmp"
	removeTemporary := true
	defer func() {
		if removeTemporary {
			_ = os.Remove(temporary)
		}
	}()
	if err := protectCollectorKey(temporary, key); err != nil {
		return err
	}
	if err := restrictCollectorKey(temporary, serviceUser); err != nil {
		return err
	}
	from, err := windows.UTF16PtrFromString(temporary)
	if err != nil {
		return err
	}
	to, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	if err = windows.MoveFile(from, to); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}

func protectCollectorKey(path, key string) error {
	plain := []byte(key)
	if len(plain) == 0 {
		return errors.New("collector credential is empty")
	}
	defer func() {
		for index := range plain {
			plain[index] = 0
		}
	}()
	in := windows.DataBlob{Size: uint32(len(plain)), Data: &plain[0]}
	var out windows.DataBlob
	if err := windows.CryptProtectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_LOCAL_MACHINE|windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	if _, err = file.Write(unsafe.Slice(out.Data, out.Size)); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

func loadCollectorKey(path string) (string, error) {
	protected, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if len(protected) == 0 {
		return "", errors.New("protected collector credential is empty")
	}
	in := windows.DataBlob{Size: uint32(len(protected)), Data: &protected[0]}
	var out windows.DataBlob
	if err = windows.CryptUnprotectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return "", err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	plain := append([]byte(nil), unsafe.Slice(out.Data, out.Size)...)
	defer func() {
		for index := range plain {
			plain[index] = 0
		}
	}()
	if len(plain) < 32 {
		return "", errors.New("protected collector credential is invalid")
	}
	return string(plain), nil
}
