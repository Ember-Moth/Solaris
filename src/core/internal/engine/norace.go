//go:build !race

package engine

// raceEnabled says that this is a race-detector build.
const raceEnabled = false

// settleFinalizers only waits in race-detector builds (race.go).
func settleFinalizers() {}
