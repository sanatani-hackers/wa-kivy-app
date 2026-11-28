package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"io"
	"math"
	"net"
	"strconv"
	"testing"
	"time"

	meowcaller "github.com/purpshell/meowcaller"
)

func TestLivePCMSourceLargeBuffer(t *testing.T) {
	if livePCMBufferCap != 150 {
		t.Fatalf("expected livePCMBufferCap == 150, got %d", livePCMBufferCap)
	}

	src, err := newLivePCMSource(6)
	if err != nil {
		t.Fatalf("newLivePCMSource failed: %v", err)
	}
	defer func() { _ = src.Close() }()

	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+strconv.Itoa(src.port), 2*time.Second)
	if err != nil {
		t.Fatalf("dial livePCMSource failed: %v", err)
	}

	// Build a single 60ms frame with constant 0.5 float32 sample (int16 = 16384)
	oneFrame := make([]byte, livePCMFrameBytes)
	for i := 0; i < meowcaller.FrameSamples; i++ {
		binary.LittleEndian.PutUint16(oneFrame[2*i:], uint16(int16(16384)))
	}

	// Send 3 frames (< prebuf=6): ReadFrame must return (nil, nil) while prebuffering
	for i := 0; i < 3; i++ {
		if _, err := conn.Write(oneFrame); err != nil {
			t.Fatalf("write prebuf partial frame %d: %v", i, err)
		}
	}
	deadline := time.Now().Add(1 * time.Second)
	for len(src.frames) < 3 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if f, rerr := src.ReadFrame(); f != nil || rerr != nil {
		t.Fatalf("expected (nil, nil) before prebuf threshold, got (%v, %v)", f != nil, rerr)
	}

	// Send 5 more frames (total 8 >= prebuf=6)
	for i := 0; i < 5; i++ {
		if _, err := conn.Write(oneFrame); err != nil {
			t.Fatalf("write frame %d: %v", i+3, err)
		}
	}
	deadline = time.Now().Add(1 * time.Second)
	for len(src.frames) < 8 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}

	for idx := 0; idx < 8; idx++ {
		f, rerr := src.ReadFrame()
		if rerr != nil || len(f) != meowcaller.FrameSamples {
			t.Fatalf("frame %d unexpected: len=%d err=%v", idx, len(f), rerr)
		}
		if math.Abs(float64(f[0]-0.5)) > 1e-4 || math.Abs(float64(f[len(f)-1]-0.5)) > 1e-4 {
			t.Fatalf("frame %d sample mismatch: first=%f last=%f", idx, f[0], f[len(f)-1])
		}
	}

	// Close socket and expect io.EOF once drained
	_ = conn.Close()
	eofSeen := false
	deadline = time.Now().Add(1 * time.Second)
	for time.Now().Before(deadline) {
		_, rerr := src.ReadFrame()
		if rerr == io.EOF {
			eofSeen = true
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if !eofSeen {
		t.Fatal("expected io.EOF after socket close")
	}

	if err := src.Close(); err != nil {
		t.Fatalf("first Close error: %v", err)
	}
	if err := src.Close(); err != nil {
		t.Fatalf("second Close error: %v", err)
	}
}

func writeFramedAU(t *testing.T, w io.Writer, au []byte) {
	t.Helper()
	var hdr [4]byte
	binary.BigEndian.PutUint32(hdr[:], uint32(len(au)))
	if _, err := w.Write(hdr[:]); err != nil {
		t.Fatalf("write AU header: %v", err)
	}
	if _, err := w.Write(au); err != nil {
		t.Fatalf("write AU payload: %v", err)
	}
}

func TestLiveVideoSourceFraming(t *testing.T) {
	vsrc, err := newLiveVideoSource(3)
	if err != nil {
		t.Fatalf("newLiveVideoSource failed: %v", err)
	}
	defer func() { _ = vsrc.Close() }()

	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+strconv.Itoa(vsrc.port), 2*time.Second)
	if err != nil {
		t.Fatalf("dial liveVideoSource failed: %v", err)
	}

	sps := []byte{0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0xC0, 0x1E}
	pps := []byte{0x00, 0x00, 0x00, 0x01, 0x68, 0xCE, 0x3C, 0x80}
	idr := []byte{0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x84, 0x21}
	pFrame := []byte{0x00, 0x00, 0x00, 0x01, 0x41, 0x9A, 0x24, 0x6C}

	au1In := append(append(append([]byte(nil), sps...), pps...), idr...)
	// Also test bare IDR without SPS/PPS after SPS/PPS were seen (config packet + IDR)
	configOnly := append(append([]byte(nil), sps...), pps...)

	writeFramedAU(t, conn, au1In)
	writeFramedAU(t, conn, pFrame)
	writeFramedAU(t, conn, configOnly)
	writeFramedAU(t, conn, idr)
	_ = conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	got1, err := vsrc.ReadAU(ctx)
	if err != nil {
		t.Fatalf("ReadAU #1 error: %v", err)
	}
	if !bytes.Equal(got1, au1In) {
		t.Fatalf("AU #1 mismatch: got %x want %x", got1, au1In)
	}

	got2, err := vsrc.ReadAU(ctx)
	if err != nil {
		t.Fatalf("ReadAU #2 error: %v", err)
	}
	if !bytes.Equal(got2, pFrame) {
		t.Fatalf("AU #2 mismatch: got %x want %x", got2, pFrame)
	}

	// Config-only packet folds SPS+PPS into the subsequent bare IDR
	got3, err := vsrc.ReadAU(ctx)
	if err != nil {
		t.Fatalf("ReadAU #3 error: %v", err)
	}
	if !bytes.Equal(got3, au1In) {
		t.Fatalf("AU #3 (prepended SPS/PPS on IDR) mismatch: got %x want %x", got3, au1In)
	}

	if _, err := vsrc.ReadAU(ctx); err != io.EOF {
		t.Fatalf("expected io.EOF after drain, got %v", err)
	}

	if err := vsrc.Close(); err != nil {
		t.Fatalf("first Close error: %v", err)
	}
	if err := vsrc.Close(); err != nil {
		t.Fatalf("second Close error: %v", err)
	}
}
