//go:build cmfa

package engine

// The cmfa tag makes mihomo's IsSafePath always true, so a SYSTEM process could read
// and write any file a profile names (docs/architecture.md §3.3 #1). No build may use it;
// this declaration does not compile.
var _ int = "the cmfa build tag disables mihomo's path sandbox"
