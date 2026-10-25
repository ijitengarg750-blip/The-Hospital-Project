package agent

import (
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

const wtsUserName = 5

var (
	wtsapi32                = windows.NewLazySystemDLL("wtsapi32.dll")
	querySessionInformation = wtsapi32.NewProc("WTSQuerySessionInformationW")
	freeWTSMemory           = wtsapi32.NewProc("WTSFreeMemory")
)

type WindowsState struct {
	mu    sync.RWMutex
	value string
}

func NewWindowsState() *WindowsState {
	state := &WindowsState{value: "signed_out"}
	sessionID := windows.WTSGetActiveConsoleSessionId()
	if sessionID != 0xffffffff {
		var buffer *uint16
		var size uint32
		ok, _, _ := querySessionInformation.Call(0, uintptr(sessionID), wtsUserName, uintptr(unsafe.Pointer(&buffer)), uintptr(unsafe.Pointer(&size)))
		if ok != 0 && buffer != nil {
			if syscall.UTF16ToString(unsafe.Slice(buffer, size/2)) != "" {
				// Fail closed after a service restart until Windows emits a lock/unlock/logon event.
				state.value = "unavailable"
			}
			freeWTSMemory.Call(uintptr(unsafe.Pointer(buffer)))
		}
	}
	return state
}

func (s *WindowsState) Get() string      { s.mu.RLock(); defer s.mu.RUnlock(); return s.value }
func (s *WindowsState) Set(value string) { s.mu.Lock(); s.value = value; s.mu.Unlock() }
