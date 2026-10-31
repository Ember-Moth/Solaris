//go:build windows && (!with_naive || !with_purego)

package main

func loadNativeRuntime(string) error { return nil }
