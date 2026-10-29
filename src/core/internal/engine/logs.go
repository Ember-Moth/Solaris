package engine

import (
	"time"

	"github.com/metacubex/mihomo/log"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
)

// WatchLogs calls emit for every mihomo log line until stop is called. emit runs on one
// goroutine and must return quickly: mihomo's logger waits for every watcher.
// mihomo logs whether or not the core runs, so parsing errors reach watchers too.
func (e *Engine) WatchLogs(emit func(*controlpb.LogEntry)) (stop func()) {
	subscription := log.Subscribe()
	done := make(chan struct{})
	go func() {
		defer close(done)
		for event := range subscription {
			emit(&controlpb.LogEntry{
				Level:      logLevelToProto(event.LogLevel),
				Message:    event.Payload,
				TimeUnixMs: uint64(time.Now().UnixMilli()),
			})
		}
	}()
	return func() {
		// UnSubscribe waits on the lock mihomo holds while it hands out a line, so the
		// loop above keeps draining until the channel closes.
		log.UnSubscribe(subscription)
		<-done
	}
}

func logLevelToProto(level log.LogLevel) controlpb.LogLevel {
	switch level {
	case log.DEBUG:
		return controlpb.LogLevel_LOG_LEVEL_DEBUG
	case log.INFO:
		return controlpb.LogLevel_LOG_LEVEL_INFO
	case log.WARNING:
		return controlpb.LogLevel_LOG_LEVEL_WARNING
	case log.ERROR:
		return controlpb.LogLevel_LOG_LEVEL_ERROR
	}
	return controlpb.LogLevel_LOG_LEVEL_UNSPECIFIED
}
