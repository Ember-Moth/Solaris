//go:build !race

package engine

// settleFinalizers only waits in race-detector builds (race.go).
func settleFinalizers() {}
