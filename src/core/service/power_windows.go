package main

import (
	"fmt"
	"runtime"
	"sync/atomic"
	"unsafe"

	"golang.org/x/sys/windows"

	"github.com/Ember-Moth/Solaris/src/core/internal/power"
)

var (
	powerDLL        = windows.NewLazySystemDLL("powrprof.dll")
	registerPower   = powerDLL.NewProc("PowerRegisterSuspendResumeNotification")
	unregisterPower = powerDLL.NewProc("PowerUnregisterSuspendResumeNotification")
)

// DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS; Context is unused, so native code retains no Go pointer.
type powerParameters struct{ callback, context uintptr }

type powerRegistration struct {
	parameters powerParameters
	pin        runtime.Pinner
	handle     windows.Handle
	active     atomic.Bool
	queue      *power.Queue
}

func watchPower(queue *power.Queue) (*powerRegistration, error) {
	if err := registerPower.Find(); err != nil {
		return nil, err
	}
	if err := unregisterPower.Find(); err != nil {
		return nil, err
	}
	registration := &powerRegistration{queue: queue}
	registration.active.Store(true)
	registration.parameters.callback = windows.NewCallback(func(_ uintptr, kind uintptr, _ uintptr) uintptr {
		registration.notify(uint32(kind))
		return 0
	})
	registration.pin.Pin(&registration.parameters)
	const deviceNotifyCallback = 2
	result, _, _ := registerPower.Call(deviceNotifyCallback, uintptr(unsafe.Pointer(&registration.parameters)), uintptr(unsafe.Pointer(&registration.handle)))
	if result != 0 {
		registration.active.Store(false)
		registration.pin.Unpin()
		return nil, fmt.Errorf("register sleep/wake notifications: %w", windows.Errno(result))
	}
	return registration, nil
}

func (r *powerRegistration) notify(kind uint32) {
	if r.active.Load() {
		r.queue.Publish(kind)
	}
}

func (r *powerRegistration) Close() error {
	if !r.active.Swap(false) {
		return nil
	}
	result, _, _ := unregisterPower.Call(uintptr(r.handle))
	runtime.KeepAlive(r)
	if result != 0 {
		return fmt.Errorf("unregister sleep/wake notifications: %w", windows.Errno(result))
	}
	r.pin.Unpin()
	return nil
}
