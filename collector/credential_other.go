//go:build !windows

package main

import "errors"

func protectCollectorKey(string, string) error {
	return errors.New("collector credential protection requires Windows DPAPI")
}

func readCollectorKey() (string, error) {
	return "", errors.New("collector credential protection requires Windows DPAPI")
}

func restrictCollectorKey(string, string) error {
	return errors.New("collector credential ACL protection requires Windows")
}

func createCollectorCredential(string, string, string) error {
	return errors.New("collector credential protection requires Windows DPAPI")
}

func loadCollectorKey(string) (string, error) {
	return "", errors.New("collector credential protection requires Windows DPAPI")
}
