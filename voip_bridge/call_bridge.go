package main

/*
#include <stdlib.h>
#include <stdbool.h>
#include "python/pythonptr.h"
*/
import "C"

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unsafe"

	meowcaller "github.com/purpshell/meowcaller"
	"github.com/purpshell/meowcaller/rtp"
	"github.com/rs/zerolog"
	"go.mau.fi/whatsmeow"
)

type callClientEntry struct {
	mu    sync.Mutex
	mc    *meowcaller.Client
	calls map[string]*callHandle
}

type callHandle struct {
	uuid      string
	id        string
	call      *meowcaller.Call
	player    *meowcaller.Player
	direction string

	mu           sync.Mutex
	audioPath    string
	audioLoop    bool
	audioGen     uint64
	audioState   string // idle | playing | paused
	audioRec     meowcaller.AudioSink
	audioRecPath string
	videoRec     meowcaller.VideoSink
	videoRecPath string
	videoCancel  context.CancelFunc
	videoPath    string
	videoLoop    bool
	ended        bool
}

var (
	callCbMu    sync.RWMutex
	callEventCb C.ptr_to_python_function_string

	callMgrMu   sync.RWMutex
	callClients = make(map[string]*callClientEntry)
)

func emitCallEvent(uuid string, payload map[string]any) {
	if payload == nil {
		return
	}
	payload["uuid"] = uuid
	payload["ts"] = time.Now().Unix()

	b, err := json.Marshal(payload)
	if err != nil {
		return
	}

	callCbMu.RLock()
	cb := callEventCb
	callCbMu.RUnlock()
	if cb == nil {
		return
	}

	cUUID := C.CString(uuid)
	defer C.free(unsafe.Pointer(cUUID))
	cJSON := C.CString(string(b))
	defer C.free(unsafe.Pointer(cJSON))
	C.call_c_func_string(cb, cUUID, cJSON)
}

func callJSONOK(extra map[string]any) *C.char {
	out := map[string]any{"ok": true}
	for k, v := range extra {
		out[k] = v
	}
	b, _ := json.Marshal(out)
	return C.CString(string(b))
}

func callJSONErr(msg string) *C.char {
	b, _ := json.Marshal(map[string]any{"ok": false, "error": msg})
	return C.CString(string(b))
}

func phaseString(p meowcaller.CallPhase) string {
	switch p {
	case meowcaller.CallPhaseIdle:
		return "idle"
	case meowcaller.CallPhaseCalling:
		return "calling"
	case meowcaller.CallPhaseRinging:
		return "ringing"
	case meowcaller.CallPhaseConnecting:
		return "connecting"
	case meowcaller.CallPhaseActive:
		return "active"
	case meowcaller.CallPhaseEnded:
		return "ended"
	case meowcaller.CallPhaseWaitingRoom:
		return "waiting_room"
	default:
		return fmt.Sprintf("unknown_%d", int(p))
	}
}

type bridgeLogWriter struct {
	uuid string
}

func (w bridgeLogWriter) Write(p []byte) (int, error) {
	txt := strings.TrimSpace(string(p))
	if txt != "" {
		emitCallEvent(w.uuid, map[string]any{
			"event": "log",
			"msg":   txt,
		})
	}
	return len(p), nil
}

func initCallClient(uuid string, wa *whatsmeow.Client) {
	if wa == nil {
		return
	}
	logger := zerolog.New(zerolog.ConsoleWriter{
		Out:        bridgeLogWriter{uuid: uuid},
		NoColor:    true,
		TimeFormat: "15:04:05",
	}).Level(zerolog.InfoLevel)

	mc := meowcaller.NewClient(wa, meowcaller.WithLogger(logger))
	entry := &callClientEntry{
		mc:    mc,
		calls: make(map[string]*callHandle),
	}

	callMgrMu.Lock()
	callClients[uuid] = entry
	callMgrMu.Unlock()

	mc.OnIncomingCall(func(call *meowcaller.Call) {
		if call == nil {
			return
		}
		h := registerCallHandle(uuid, entry, call, "in")
		emitCallEvent(uuid, map[string]any{
			"event":     "incoming",
			"call_id":   call.ID(),
			"peer":      call.Peer().String(),
			"direction": "in",
			"is_video":  call.IsVideo(),
			"phase":     phaseString(call.State()),
		})
		_ = h
	})
}

func cleanupCallClient(uuid string) {
	callMgrMu.Lock()
	entry, ok := callClients[uuid]
	if ok {
		delete(callClients, uuid)
	}
	callMgrMu.Unlock()
	if !ok || entry == nil {
		return
	}
	entry.mu.Lock()
	handles := make([]*callHandle, 0, len(entry.calls))
	for _, h := range entry.calls {
		handles = append(handles, h)
	}
	entry.calls = make(map[string]*callHandle)
	entry.mu.Unlock()

	for _, h := range handles {
		h.cleanup("client_stopped")
		if h.call != nil && h.call.State() != meowcaller.CallPhaseEnded {
			_ = h.call.Hangup()
		}
	}
}

func registerCallHandle(uuid string, entry *callClientEntry, call *meowcaller.Call, direction string) *callHandle {
	player := meowcaller.NewPlayer()
	call.Subscribe(player)

	h := &callHandle{
		uuid:       uuid,
		id:         call.ID(),
		call:       call,
		player:     player,
		direction:  direction,
		audioState: "idle",
	}

	entry.mu.Lock()
	entry.calls[call.ID()] = h
	entry.mu.Unlock()

	call.OnReady(func() {
		emitCallEvent(uuid, map[string]any{
			"event":     "ready",
			"call_id":   call.ID(),
			"peer":      call.Peer().String(),
			"direction": direction,
			"is_video":  call.IsVideo(),
			"phase":     phaseString(call.State()),
		})
	})

	call.OnPeerAccept(func() {
		emitCallEvent(uuid, map[string]any{
			"event":     "peer_accept",
			"call_id":   call.ID(),
			"peer":      call.Peer().String(),
			"direction": direction,
			"is_video":  call.IsVideo(),
			"phase":     phaseString(call.State()),
		})
	})

	call.OnStateChange(func(phase meowcaller.CallPhase) {
		emitCallEvent(uuid, map[string]any{
			"event":     "state",
			"call_id":   call.ID(),
			"peer":      call.Peer().String(),
			"direction": direction,
			"is_video":  call.IsVideo(),
			"phase":     phaseString(phase),
		})
	})

	call.OnMuteState(func(muted bool) {
		emitCallEvent(uuid, map[string]any{
			"event":   "mute_state",
			"call_id": call.ID(),
			"muted":   muted,
		})
	})

	call.OnVideoState(func(vs meowcaller.VideoState) {
		emitCallEvent(uuid, map[string]any{
			"event":         "video_state",
			"call_id":       call.ID(),
			"peer":          call.Peer().String(),
			"video_active":  vs.Active,
			"video_upgrade": vs.Upgrade,
			"orientation":   vs.Orientation,
			"raw":           vs.Raw,
		})
	})

	call.OnEnd(func(reason string) {
		h.cleanup(reason)
		entry.mu.Lock()
		delete(entry.calls, call.ID())
		entry.mu.Unlock()

		emitCallEvent(uuid, map[string]any{
			"event":     "end",
			"call_id":   call.ID(),
			"peer":      call.Peer().String(),
			"direction": direction,
			"is_video":  call.IsVideo(),
			"reason":    reason,
			"phase":     "ended",
		})
	})

	return h
}

func (h *callHandle) cleanup(reason string) {
	h.mu.Lock()
	if h.ended {
		h.mu.Unlock()
		return
	}
	h.ended = true
	h.audioLoop = false
	h.audioGen++
	h.audioState = "idle"
	vCancel := h.videoCancel
	h.videoCancel = nil
	aRec := h.audioRec
	h.audioRec = nil
	vRec := h.videoRec
	h.videoRec = nil
	h.mu.Unlock()

	if vCancel != nil {
		vCancel()
	}
	if h.player != nil {
		h.player.OnFinish(nil)
		h.player.Stop()
	}
	if aRec != nil {
		_ = aRec.Close()
	}
	if vRec != nil {
		_ = vRec.Close()
	}
}

func getCallClient(uuid string) (*callClientEntry, error) {
	callMgrMu.RLock()
	entry := callClients[uuid]
	callMgrMu.RUnlock()
	if entry == nil || entry.mc == nil {
		return nil, errors.New("call engine not initialized for session")
	}
	return entry, nil
}

func getCallHandle(uuid, callID string) (*callClientEntry, *callHandle, error) {
	entry, err := getCallClient(uuid)
	if err != nil {
		return nil, nil, err
	}
	entry.mu.Lock()
	h := entry.calls[callID]
	entry.mu.Unlock()
	if h == nil || h.call == nil {
		return entry, nil, fmt.Errorf("active call not found: %s", callID)
	}
	return entry, h, nil
}

func openAudioSource(path string) (meowcaller.AudioSource, error) {
	// Inspect first 16 bytes for format detection before trusting extension
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	var hdr [16]byte
	n, _ := f.Read(hdr[:])
	_ = f.Close()
	magic := hdr[:n]
	ext := strings.ToLower(filepath.Ext(path))

	switch {
	case len(magic) >= 12 && bytes.Equal(magic[:4], []byte("RIFF")) && bytes.Equal(magic[8:12], []byte("WAVE")):
		return meowcaller.WAVFile(path)
	case len(magic) >= 4 && bytes.Equal(magic[:4], []byte("OggS")):
		return meowcaller.OpusFile(path)
	case len(magic) >= 3 && bytes.Equal(magic[:3], []byte("ID3")),
		len(magic) >= 2 && magic[0] == 0xFF && (magic[1]&0xE0) == 0xE0:
		return meowcaller.MP3File(path)
	case ext == ".wav":
		return meowcaller.WAVFile(path)
	case ext == ".ogg" || ext == ".opus":
		return meowcaller.OpusFile(path)
	default:
		if src, err := meowcaller.MP3File(path); err == nil {
			return src, nil
		}
		if src, err := meowcaller.WAVFile(path); err == nil {
			return src, nil
		}
		return meowcaller.OpusFile(path)
	}
}

func loadH264AccessUnits(h264Path string) ([][]byte, error) {
	raw, err := os.ReadFile(h264Path)
	if err != nil {
		return nil, err
	}
	nalus := rtp.SplitAnnexB(raw)
	if len(nalus) == 0 {
		return nil, fmt.Errorf("no H.264 NAL units found in %s", h264Path)
	}

	var sps, pps []byte
	var aus [][]byte
	var pending [][]byte

	ensureParams := func(au [][]byte) [][]byte {
		if sps == nil && pps == nil {
			return au
		}
		var hasSPS, hasPPS bool
		for _, n := range au {
			if len(n) == 0 {
				continue
			}
			switch n[0] & 0x1F {
			case 7:
				hasSPS = true
			case 8:
				hasPPS = true
			}
		}
		var prefix [][]byte
		if !hasSPS && sps != nil {
			prefix = append(prefix, sps)
		}
		if !hasPPS && pps != nil {
			prefix = append(prefix, pps)
		}
		if len(prefix) == 0 {
			return au
		}
		return append(prefix, au...)
	}

	joinAnnexB := func(items [][]byte) []byte {
		var buf bytes.Buffer
		for _, n := range items {
			buf.Write([]byte{0x00, 0x00, 0x00, 0x01})
			buf.Write(n)
		}
		return buf.Bytes()
	}

	for _, n := range nalus {
		if len(n) == 0 {
			continue
		}
		naluType := n[0] & 0x1F
		switch naluType {
		case 7:
			sps = append([]byte(nil), n...)
		case 8:
			pps = append([]byte(nil), n...)
		}
		pending = append(pending, n)
		if naluType == 1 || naluType == 5 {
			if naluType == 5 {
				pending = ensureParams(pending)
			}
			aus = append(aus, joinAnnexB(pending))
			pending = nil
		}
	}
	if len(pending) > 0 {
		aus = append(aus, joinAnnexB(pending))
	}
	if len(aus) == 0 {
		return nil, fmt.Errorf("no H.264 access units built from %s", h264Path)
	}
	return aus, nil
}

// --------------------------------------------------------------------------- Exported C FFI Functions

//export CallRegisterCallback
func CallRegisterCallback(cb C.ptr_to_python_function_string) *C.char {
	callCbMu.Lock()
	callEventCb = cb
	callCbMu.Unlock()
	return callJSONOK(nil)
}

//export CallPlace
func CallPlace(id *C.char, target *C.char, isVideo C.int) *C.char {
	uuid := C.GoString(id)
	tgt := strings.TrimSpace(C.GoString(target))
	if tgt == "" {
		return callJSONErr("target number/JID required")
	}
	entry, err := getCallClient(uuid)
	if err != nil {
		return callJSONErr(err.Error())
	}

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()

	call, err := entry.mc.CallWithOptions(ctx, tgt, meowcaller.CallOptions{Video: isVideo != 0})
	if err != nil {
		return callJSONErr(err.Error())
	}
	registerCallHandle(uuid, entry, call, "out")
	return callJSONOK(map[string]any{
		"call_id":  call.ID(),
		"peer":     call.Peer().String(),
		"is_video": isVideo != 0,
		"phase":    phaseString(call.State()),
	})
}

//export CallAnswer
func CallAnswer(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.Answer(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id, "phase": phaseString(h.call.State())})
}

//export CallDecline
func CallDecline(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.Reject(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallHangup
func CallHangup(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.Hangup(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallPlayAudio
func CallPlayAudio(id *C.char, callID *C.char, path *C.char, loop C.int) *C.char {
	uuid := C.GoString(id)
	cid := C.GoString(callID)
	filePath := strings.TrimSpace(C.GoString(path))
	if filePath == "" {
		return callJSONErr("audio file path required")
	}

	_, h, err := getCallHandle(uuid, cid)
	if err != nil {
		return callJSONErr(err.Error())
	}
	src, err := openAudioSource(filePath)
	if err != nil {
		return callJSONErr(fmt.Sprintf("decode audio failed: %v", err))
	}

	h.mu.Lock()
	if h.ended {
		h.mu.Unlock()
		_ = src.Close()
		return callJSONErr("call already ended")
	}
	h.audioGen++
	myGen := h.audioGen
	h.audioPath = filePath
	h.audioLoop = loop != 0
	h.audioState = "playing"
	h.mu.Unlock()

	var attachFinish func(gen uint64)
	attachFinish = func(gen uint64) {
		h.player.OnFinish(func() {
			go func() {
				h.mu.Lock()
				stillMine := !h.ended && h.audioGen == gen
				shouldLoop := stillMine && h.audioLoop
				curPath := h.audioPath
				if stillMine && !shouldLoop {
					h.audioState = "idle"
				}
				h.mu.Unlock()

				if !stillMine {
					return
				}
				if shouldLoop {
					nextSrc, openErr := openAudioSource(curPath)
					if openErr == nil {
						h.mu.Lock()
						valid := !h.ended && h.audioGen == gen && h.audioLoop
						h.mu.Unlock()
						if valid {
							attachFinish(gen)
							h.player.Play(nextSrc)
							return
						}
						_ = nextSrc.Close()
					}
					emitCallEvent(uuid, map[string]any{
						"event":   "audio_error",
						"call_id": cid,
						"path":    curPath,
						"error":   fmt.Sprintf("%v", openErr),
					})
					return
				}
				emitCallEvent(uuid, map[string]any{
					"event":   "audio_finish",
					"call_id": cid,
					"path":    curPath,
				})
			}()
		})
	}

	attachFinish(myGen)
	h.player.Play(src)

	return callJSONOK(map[string]any{
		"call_id": cid,
		"path":    filePath,
		"loop":    loop != 0,
	})
}

//export CallPauseAudio
func CallPauseAudio(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	h.audioState = "paused"
	h.mu.Unlock()
	h.player.Pause()
	return callJSONOK(map[string]any{"call_id": h.id, "audio_state": "paused"})
}

//export CallResumeAudio
func CallResumeAudio(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	h.audioState = "playing"
	h.mu.Unlock()
	h.player.Resume()
	return callJSONOK(map[string]any{"call_id": h.id, "audio_state": "playing"})
}

//export CallStopAudio
func CallStopAudio(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	h.audioLoop = false
	h.audioGen++
	h.audioState = "idle"
	h.audioPath = ""
	h.mu.Unlock()
	h.player.OnFinish(nil)
	h.player.Stop()
	return callJSONOK(map[string]any{"call_id": h.id, "audio_state": "idle"})
}

//export CallPlayVideo
func CallPlayVideo(id *C.char, callID *C.char, h264Path *C.char, fps C.int, loop C.int) *C.char {
	return CallPlayAV(id, callID, h264Path, nil, fps, loop)
}

//export CallPlayAV
func CallPlayAV(id *C.char, callID *C.char, h264Path *C.char, audioPath *C.char, fps C.int, loop C.int) *C.char {
	uuid := C.GoString(id)
	cid := C.GoString(callID)
	vPath := strings.TrimSpace(C.GoString(h264Path))
	aPath := ""
	if audioPath != nil {
		aPath = strings.TrimSpace(C.GoString(audioPath))
	}
	if vPath == "" {
		return callJSONErr("H.264 file path required")
	}
	fpsInt := int(fps)
	if fpsInt <= 0 || fpsInt > 60 {
		fpsInt = 15
	}

	_, h, err := getCallHandle(uuid, cid)
	if err != nil {
		return callJSONErr(err.Error())
	}
	aus, err := loadH264AccessUnits(vPath)
	if err != nil {
		return callJSONErr(err.Error())
	}

	var firstAudioSrc meowcaller.AudioSource
	if aPath != "" {
		firstAudioSrc, err = openAudioSource(aPath)
		if err != nil {
			emitCallEvent(uuid, map[string]any{
				"event":   "audio_error",
				"call_id": cid,
				"path":    aPath,
				"error":   fmt.Sprintf("video audio decode failed: %v", err),
			})
			aPath = ""
		}
	}

	ctx, cancel := context.WithCancel(context.Background())

	h.mu.Lock()
	if h.ended {
		h.mu.Unlock()
		cancel()
		if firstAudioSrc != nil {
			_ = firstAudioSrc.Close()
		}
		return callJSONErr("call already ended")
	}
	oldCancel := h.videoCancel
	h.videoCancel = cancel
	h.videoPath = vPath
	h.videoLoop = loop != 0
	var myAudioGen uint64
	if firstAudioSrc != nil {
		h.audioGen++
		myAudioGen = h.audioGen
		h.audioPath = aPath
		h.audioLoop = loop != 0
		h.audioState = "playing"
	}
	h.mu.Unlock()

	if oldCancel != nil {
		oldCancel()
	}

	frameDur := time.Second / time.Duration(fpsInt)
	shouldLoop := loop != 0

	var attachAudioLoop func(gen uint64)
	attachAudioLoop = func(gen uint64) {
		if aPath == "" {
			return
		}
		h.player.OnFinish(func() {
			go func() {
				h.mu.Lock()
				stillMine := !h.ended && h.audioGen == gen
				wantLoop := stillMine && h.audioLoop
				curA := h.audioPath
				if stillMine && !wantLoop {
					h.audioState = "idle"
				}
				h.mu.Unlock()
				if !stillMine || !wantLoop || curA == "" {
					return
				}
				if nxt, oerr := openAudioSource(curA); oerr == nil {
					h.mu.Lock()
					ok := !h.ended && h.audioGen == gen && h.audioLoop
					h.mu.Unlock()
					if ok {
						attachAudioLoop(gen)
						h.player.Play(nxt)
						return
					}
					_ = nxt.Close()
				}
			}()
		})
	}

	go func() {
		defer cancel()
		// Poll every 10ms until call becomes Active so video and audio start locked to t0
		for i := 0; i < 1500; i++ {
			if ctx.Err() != nil {
				if firstAudioSrc != nil {
					_ = firstAudioSrc.Close()
				}
				return
			}
			st := h.call.State()
			if st == meowcaller.CallPhaseActive {
				break
			}
			if st == meowcaller.CallPhaseEnded {
				if firstAudioSrc != nil {
					_ = firstAudioSrc.Close()
				}
				return
			}
			time.Sleep(10 * time.Millisecond)
		}

		// Launch synchronized audio & video right when Active begins
		if firstAudioSrc != nil {
			h.mu.Lock()
			valid := !h.ended && h.audioGen == myAudioGen
			h.mu.Unlock()
			if valid {
				attachAudioLoop(myAudioGen)
				h.player.Play(firstAudioSrc)
			} else {
				_ = firstAudioSrc.Close()
			}
		}

		startTime := time.Now()
		var sentCount int64

		for {
			for idx, au := range aus {
				if ctx.Err() != nil || h.call.State() == meowcaller.CallPhaseEnded {
					return
				}
				if err := h.call.SendVideoWithDuration(au, frameDur); err != nil {
					if idx == 0 {
						emitCallEvent(uuid, map[string]any{
							"event":   "video_error",
							"call_id": cid,
							"error":   err.Error(),
						})
					}
					if h.call.State() == meowcaller.CallPhaseEnded {
						return
					}
				}
				sentCount++
				targetAt := startTime.Add(time.Duration(sentCount) * frameDur)
				if waitDur := time.Until(targetAt); waitDur > 0 {
					select {
					case <-ctx.Done():
						return
					case <-time.After(waitDur):
					}
				} else if waitDur < -500*time.Millisecond {
					// Reset baseline clock if relay stalled so we don't fast-forward burst frames
					startTime = time.Now()
					sentCount = 0
				}
			}
			if !shouldLoop {
				emitCallEvent(uuid, map[string]any{
					"event":   "video_finish",
					"call_id": cid,
					"path":    vPath,
				})
				return
			}
		}
	}()

	return callJSONOK(map[string]any{
		"call_id":    cid,
		"path":       vPath,
		"audio_path": aPath,
		"frames":     len(aus),
		"fps":        fpsInt,
		"loop":       shouldLoop,
	})
}

//export CallStopVideoStream
func CallStopVideoStream(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	cancel := h.videoCancel
	h.videoCancel = nil
	h.videoPath = ""
	h.videoLoop = false
	h.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallStartVideo
func CallStartVideo(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.StartVideo(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallAcceptVideo
func CallAcceptVideo(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.AcceptVideo(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallStopVideo
func CallStopVideo(id *C.char, callID *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.StopVideo(); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallRecordAudio
func CallRecordAudio(id *C.char, callID *C.char, wavPath *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	outPath := strings.TrimSpace(C.GoString(wavPath))
	if outPath == "" {
		h.mu.Lock()
		old := h.audioRec
		h.audioRec = nil
		h.audioRecPath = ""
		h.mu.Unlock()
		h.call.Receive(nil)
		if old != nil {
			_ = old.Close()
		}
		return callJSONOK(map[string]any{"call_id": h.id, "recording": false})
	}

	sink, err := meowcaller.WAVRecorder(outPath)
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	old := h.audioRec
	h.audioRec = sink
	h.audioRecPath = outPath
	h.mu.Unlock()
	if old != nil {
		_ = old.Close()
	}
	h.call.Receive(sink)
	return callJSONOK(map[string]any{"call_id": h.id, "recording": true, "path": outPath})
}

//export CallRecordVideo
func CallRecordVideo(id *C.char, callID *C.char, h264Path *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	outPath := strings.TrimSpace(C.GoString(h264Path))
	if outPath == "" {
		h.mu.Lock()
		old := h.videoRec
		h.videoRec = nil
		h.videoRecPath = ""
		h.mu.Unlock()
		h.call.ReceiveVideo(nil)
		if old != nil {
			_ = old.Close()
		}
		return callJSONOK(map[string]any{"call_id": h.id, "recording": false})
	}

	sink, err := meowcaller.AnnexBRecorder(outPath)
	if err != nil {
		return callJSONErr(err.Error())
	}
	h.mu.Lock()
	old := h.videoRec
	h.videoRec = sink
	h.videoRecPath = outPath
	h.mu.Unlock()
	if old != nil {
		_ = old.Close()
	}
	h.call.ReceiveVideo(sink)
	return callJSONOK(map[string]any{"call_id": h.id, "recording": true, "path": outPath})
}

//export CallSendReaction
func CallSendReaction(id *C.char, callID *C.char, emoji *C.char) *C.char {
	_, h, err := getCallHandle(C.GoString(id), C.GoString(callID))
	if err != nil {
		return callJSONErr(err.Error())
	}
	if err := h.call.SendReaction(C.GoString(emoji)); err != nil {
		return callJSONErr(err.Error())
	}
	return callJSONOK(map[string]any{"call_id": h.id})
}

//export CallListActive
func CallListActive(id *C.char) *C.char {
	entry, err := getCallClient(C.GoString(id))
	if err != nil {
		return callJSONOK(map[string]any{"calls": []any{}})
	}
	entry.mu.Lock()
	items := make([]map[string]any, 0, len(entry.calls))
	for _, h := range entry.calls {
		h.mu.Lock()
		items = append(items, map[string]any{
			"call_id":     h.id,
			"peer":        h.call.Peer().String(),
			"direction":   h.direction,
			"phase":       phaseString(h.call.State()),
			"is_video":    h.call.IsVideo(),
			"audio_state": h.audioState,
			"audio_path":  h.audioPath,
			"audio_loop":  h.audioLoop,
			"video_path":  h.videoPath,
			"recording":   h.audioRec != nil,
		})
		h.mu.Unlock()
	}
	entry.mu.Unlock()
	return callJSONOK(map[string]any{"calls": items})
}
