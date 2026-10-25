package agent

import (
	"context"
	"crypto/des"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"time"
)

const tunnelReady = "HOSPITAL-RFB/1\n"

func GenerateMachineIdentity(machineCode string) (certificatePEM, privateKeyPEM string, err error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return "", "", err
	}
	now := time.Now()
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(now.UnixNano()),
		Subject:               pkix.Name{CommonName: machineCode},
		DNSNames:              []string{machineCode},
		NotBefore:             now.Add(-time.Hour),
		NotAfter:              now.AddDate(1, 0, 0),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return "", "", err
	}
	certificate := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	privateKey := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	return string(certificate), string(privateKey), nil
}

func tunnelTLSConfig(config Config) (*tls.Config, error) {
	identity, err := tls.X509KeyPair([]byte(config.TunnelCertificate), []byte(config.TunnelPrivateKey))
	if err != nil {
		return nil, fmt.Errorf("load tunnel identity: %w", err)
	}
	clientRoots := x509.NewCertPool()
	if !clientRoots.AppendCertsFromPEM([]byte(config.GatewayCACertificate)) {
		return nil, errors.New("gateway CA certificate is invalid")
	}
	return &tls.Config{
		Certificates: []tls.Certificate{identity},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    clientRoots,
		MinVersion:   tls.VersionTLS12,
	}, nil
}

func readExact(connection net.Conn, size int) ([]byte, error) {
	result := make([]byte, size)
	_, err := io.ReadFull(connection, result)
	return result, err
}

func reverseBits(value byte) byte {
	value = ((value & 0x55) << 1) | ((value >> 1) & 0x55)
	value = ((value & 0x33) << 2) | ((value >> 2) & 0x33)
	return (value << 4) | (value >> 4)
}

func authenticateLocalVNC(connection net.Conn, password string) error {
	banner, err := readExact(connection, 12)
	var serverMinor int
	if err != nil || len(banner) != 12 {
		return errors.New("invalid VNC protocol banner")
	}
	if count, scanErr := fmt.Sscanf(string(banner), "RFB 003.%03d\n", &serverMinor); scanErr != nil || count != 1 || serverMinor < 3 {
		return errors.New("invalid VNC protocol banner")
	}
	clientMinor := 8
	if serverMinor < 8 {
		clientMinor = 7
	}
	if serverMinor < 7 {
		clientMinor = 3
	}
	if _, err = connection.Write([]byte(fmt.Sprintf("RFB 003.%03d\n", clientMinor))); err != nil {
		return err
	}
	if clientMinor == 3 {
		security, readErr := readExact(connection, 4)
		if readErr != nil || binary.BigEndian.Uint32(security) != 2 {
			return errors.New("VNC server did not offer type 2 authentication")
		}
	} else {
		count, readErr := readExact(connection, 1)
		if readErr != nil || count[0] == 0 {
			return errors.New("VNC server did not offer authentication")
		}
		types, readErr := readExact(connection, int(count[0]))
		if readErr != nil {
			return readErr
		}
		found := false
		for _, value := range types {
			found = found || value == 2
		}
		if !found {
			return errors.New("VNC server did not offer type 2 authentication")
		}
		if _, err = connection.Write([]byte{2}); err != nil {
			return err
		}
	}
	challenge, err := readExact(connection, 16)
	if err != nil {
		return err
	}
	passwordBytes := []byte(password)
	if len(passwordBytes) < 1 || len(passwordBytes) > 8 {
		return errors.New("VNC password must be 1 to 8 bytes")
	}
	key := make([]byte, 8)
	copy(key, passwordBytes)
	for index := range key {
		key[index] = reverseBits(key[index])
	}
	cipher, err := des.NewCipher(key)
	if err != nil {
		return err
	}
	response := make([]byte, 16)
	cipher.Encrypt(response[:8], challenge[:8])
	cipher.Encrypt(response[8:], challenge[8:])
	if _, err = connection.Write(response); err != nil {
		return err
	}
	status, err := readExact(connection, 4)
	if err != nil || binary.BigEndian.Uint32(status) != 0 {
		return errors.New("VNC authentication failed")
	}
	return nil
}

func serveTunnelConnection(client net.Conn, config Config) {
	defer client.Close()
	if tlsClient, ok := client.(*tls.Conn); ok {
		if err := tlsClient.Handshake(); err != nil {
			return
		}
	}
	upstream, err := net.DialTimeout("tcp", config.VNCAddress, 5*time.Second)
	if err != nil {
		return
	}
	defer upstream.Close()
	if err = authenticateLocalVNC(upstream, config.VNCPassword); err != nil {
		return
	}
	if _, err = client.Write([]byte(tunnelReady)); err != nil {
		return
	}
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(upstream, client); done <- struct{}{} }()
	go func() { _, _ = io.Copy(client, upstream); done <- struct{}{} }()
	<-done
}

func ServeTunnel(ctx context.Context, listener net.Listener, config Config) error {
	tlsConfig, err := tunnelTLSConfig(config)
	if err != nil {
		return err
	}
	secure := tls.NewListener(listener, tlsConfig)
	go func() {
		<-ctx.Done()
		_ = secure.Close()
	}()
	for {
		connection, err := secure.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		go serveTunnelConnection(connection, config)
	}
}

func RunTunnel(ctx context.Context, config Config) error {
	listener, err := net.Listen("tcp", config.TunnelListen)
	if err != nil {
		return err
	}
	return ServeTunnel(ctx, listener, config)
}
