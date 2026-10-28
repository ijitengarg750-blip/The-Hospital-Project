package agent

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"testing"
	"time"
)

func gatewayIdentity(t *testing.T) (tls.Certificate, string) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "gateway-test"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageCertSign,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, BasicConstraintsValid: true, IsCA: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	identity, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	return identity, string(certPEM)
}

func fakeVNC(t *testing.T) (string, <-chan byte) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	shared := make(chan byte, 1)
	go func() {
		defer listener.Close()
		connection, err := listener.Accept()
		if err != nil {
			return
		}
		defer connection.Close()
		_, _ = connection.Write([]byte("RFB 003.008\n"))
		_, _ = io.ReadFull(connection, make([]byte, 12))
		_, _ = connection.Write([]byte{1, 2})
		_, _ = io.ReadFull(connection, make([]byte, 1))
		_, _ = connection.Write(make([]byte, 16))
		_, _ = io.ReadFull(connection, make([]byte, 16))
		_, _ = connection.Write(make([]byte, 4))
		value := make([]byte, 1)
		_, _ = io.ReadFull(connection, value)
		shared <- value[0]
		payload := append([]byte{}, 0, 1, 0, 1)
		payload = append(payload, make([]byte, 16)...)
		name := []byte("test")
		length := make([]byte, 4)
		binary.BigEndian.PutUint32(length, uint32(len(name)))
		payload = append(payload, length...)
		payload = append(payload, name...)
		_, _ = connection.Write(payload)
	}()
	return listener.Addr().String(), shared
}

func TestTunnelEncryptsVNCAndRequiresGatewayCertificate(t *testing.T) {
	agentCert, agentKey, err := GenerateMachineIdentity("SIRIUS-2")
	if err != nil {
		t.Fatal(err)
	}
	gatewayCert, gatewayCA := gatewayIdentity(t)
	vncAddress, shared := fakeVNC(t)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	config := Config{TunnelCertificate: agentCert, TunnelPrivateKey: agentKey, GatewayCACertificate: gatewayCA, VNCAddress: vncAddress, VNCPassword: "correct"}
	go func() { _ = ServeTunnel(ctx, listener, config) }()

	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM([]byte(agentCert)) {
		t.Fatal("agent certificate")
	}
	connection, err := tls.Dial("tcp", listener.Addr().String(), &tls.Config{
		Certificates: []tls.Certificate{gatewayCert}, RootCAs: roots,
		ServerName: "SIRIUS-2", MinVersion: tls.VersionTLS12,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	ready := make([]byte, len(tunnelReady))
	if _, err = io.ReadFull(connection, ready); err != nil || string(ready) != tunnelReady {
		t.Fatalf("ready: %q %v", ready, err)
	}
	_, _ = connection.Write([]byte{1})
	response := make([]byte, 28)
	if _, err = io.ReadFull(connection, response); err != nil {
		t.Fatal(err)
	}
	if string(response[len(response)-4:]) != "test" {
		t.Fatalf("unexpected server init: %x", response)
	}
	if value := <-shared; value != 1 {
		t.Fatalf("shared flag = %d", value)
	}
}

func TestTunnelRejectsUnencryptedConnections(t *testing.T) {
	agentCert, agentKey, err := GenerateMachineIdentity("SIRIUS-2")
	if err != nil {
		t.Fatal(err)
	}
	_, gatewayCA := gatewayIdentity(t)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		_ = ServeTunnel(ctx, listener, Config{TunnelCertificate: agentCert, TunnelPrivateKey: agentKey, GatewayCACertificate: gatewayCA})
	}()

	plain, err := net.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer plain.Close()
	_ = plain.SetDeadline(time.Now().Add(time.Second))
	_, _ = plain.Write([]byte("unencrypted"))
	buffer := make([]byte, len(tunnelReady))
	if count, _ := plain.Read(buffer); count != 0 {
		t.Fatalf("plaintext received tunnel data: %q", buffer[:count])
	}
}

func TestLocalVNCAuthenticationSupportsLegacyRFB33(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	done := make(chan error, 1)
	go func() {
		defer server.Close()
		_, _ = server.Write([]byte("RFB 003.003\n"))
		version := make([]byte, 12)
		_, _ = io.ReadFull(server, version)
		if string(version) != "RFB 003.003\n" {
			done <- errors.New("client did not negotiate RFB 3.3")
			return
		}
		security := make([]byte, 4)
		binary.BigEndian.PutUint32(security, 2)
		_, _ = server.Write(security)
		_, _ = server.Write(make([]byte, 16))
		_, _ = io.ReadFull(server, make([]byte, 16))
		_, _ = server.Write(make([]byte, 4))
		done <- nil
	}()

	if err := authenticateLocalVNC(client, "correct"); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestCheckLocalVNCRequiresAUsableAuthenticatedServer(t *testing.T) {
	address, _ := fakeVNC(t)
	if err := CheckLocalVNC(context.Background(), address, "correct"); err != nil {
		t.Fatal(err)
	}
	if err := CheckLocalVNC(context.Background(), "127.0.0.1:1", "correct"); err == nil {
		t.Fatal("unreachable VNC server reported ready")
	}
}
