// audio_switch.swift
// 사용법:
//   audio_switch list              → 모든 출력 장치 목록 (ID TAB 이름)
//   audio_switch get               → 현재 기본 출력 장치 ID 출력
//   audio_switch set <deviceID>    → 기본 출력 장치 변경
//   audio_switch info <deviceID>   → 장치 이름 + 서브 장치 목록 출력
//   audio_switch create-multi <id1> <id2> ...  → Multi-Output Device 생성, 새 ID 출력
//   audio_switch destroy <deviceID>            → Aggregate 장치 삭제
//
// 별도 권한 불필요 (CoreAudio 표준 API만 사용)

import Foundation
import CoreAudio

// MARK: - 헬퍼

func getDefaultOutputID() -> AudioDeviceID? {
    var id: AudioDeviceID = 0
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    return AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &id
    ) == noErr ? id : nil
}

func setDefaultOutputID(_ id: AudioDeviceID) -> Bool {
    var mutableID = id
    let size = UInt32(MemoryLayout<AudioDeviceID>.size)
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    return AudioObjectSetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, size, &mutableID
    ) == noErr
}

func getDeviceName(_ id: AudioDeviceID) -> String {
    var cfStr: Unmanaged<CFString>? = nil
    var size = UInt32(MemoryLayout<CFString>.size)
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    withUnsafeMutablePointer(to: &cfStr) { ptr in
        _ = AudioObjectGetPropertyData(id, &addr, 0, nil, &size,
                                       UnsafeMutableRawPointer(ptr))
    }
    return cfStr?.takeRetainedValue() as String? ?? "?"
}

func getDeviceUID(_ id: AudioDeviceID) -> String {
    var cfStr: Unmanaged<CFString>? = nil
    var size = UInt32(MemoryLayout<CFString>.size)
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceUID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    withUnsafeMutablePointer(to: &cfStr) { ptr in
        _ = AudioObjectGetPropertyData(id, &addr, 0, nil, &size,
                                       UnsafeMutableRawPointer(ptr))
    }
    return cfStr?.takeRetainedValue() as String? ?? ""
}

func hasOutputStreams(_ id: AudioDeviceID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreamConfiguration,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    return AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr && size > 0
}

func getAllDeviceIDs() -> [AudioDeviceID] {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size
    ) == noErr else { return [] }

    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    var ids = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids
    ) == noErr else { return [] }

    return ids
}

func getAllOutputDeviceIDs() -> [AudioDeviceID] {
    return getAllDeviceIDs().filter { hasOutputStreams($0) }
}

// kAudioAggregateDevicePropertyFullSubDeviceList는 AudioDeviceID 배열이 아니라
// 서브 장치 UID(CFString)들의 CFArray를 반환한다. 예전 코드는 이를 AudioDeviceID로
// 잘못 캐스팅해 의미 없는 값을 출력했다 (예: info 명령에서 "?"로 표시됨).
func getSubDeviceUIDs(_ aggregateID: AudioDeviceID) -> [String] {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioAggregateDevicePropertyFullSubDeviceList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(aggregateID, &addr, 0, nil, &size) == noErr,
          size > 0 else { return [] }

    var cfArray: Unmanaged<CFArray>? = nil
    withUnsafeMutablePointer(to: &cfArray) { ptr in
        _ = AudioObjectGetPropertyData(aggregateID, &addr, 0, nil, &size,
                                       UnsafeMutableRawPointer(ptr))
    }
    guard let array = cfArray?.takeRetainedValue() else { return [] }
    return (array as NSArray).compactMap { $0 as? String }
}

func getSubDeviceIDs(_ aggregateID: AudioDeviceID) -> [AudioDeviceID] {
    let uids = getSubDeviceUIDs(aggregateID)
    guard !uids.isEmpty else { return [] }

    var uidToID: [String: AudioDeviceID] = [:]
    for id in getAllDeviceIDs() {
        let uid = getDeviceUID(id)
        if !uid.isEmpty { uidToID[uid] = id }
    }
    return uids.compactMap { uidToID[$0] }
}

// MARK: - Multi-Output Device 생성/삭제

func createMultiOutputDevice(deviceIDs: [AudioDeviceID]) -> AudioDeviceID? {
    var subDeviceList: [[String: Any]] = []
    for id in deviceIDs {
        let uid = getDeviceUID(id)
        guard !uid.isEmpty else { continue }
        subDeviceList.append([kAudioSubDeviceUIDKey as String: uid])
    }
    guard !subDeviceList.isEmpty else { return nil }

    let desc: NSDictionary = [
        kAudioAggregateDeviceNameKey as String:        "ZoomTrans 캡처",
        kAudioAggregateDeviceUIDKey as String:         "com.zoomtrans.multiout.\(Int.random(in: 10000...99999))",
        kAudioAggregateDeviceIsStackedKey as String:   1,   // Multi-Output (스택형)
        kAudioAggregateDeviceSubDeviceListKey as String: subDeviceList,
    ]

    var newID: AudioDeviceID = 0
    let status = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &newID)
    return status == noErr ? newID : nil
}

func destroyAggregateDevice(_ id: AudioDeviceID) -> Bool {
    return AudioHardwareDestroyAggregateDevice(id) == noErr
}

// MARK: - main

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("사용법: audio_switch list | get | set <id> | info <id> | create-multi <id...> | destroy <id>\n", stderr)
    exit(1)
}

switch args[1] {

case "list":
    for id in getAllOutputDeviceIDs() {
        print("\(id)\t\(getDeviceName(id))")
    }

case "get":
    if let id = getDefaultOutputID() {
        print(id)
    } else {
        fputs("오류: 현재 출력 장치를 가져올 수 없습니다.\n", stderr); exit(1)
    }

case "set":
    guard args.count >= 3, let id = AudioDeviceID(args[2]) else {
        fputs("오류: 장치 ID 필요\n", stderr); exit(1)
    }
    if setDefaultOutputID(id) {
        print("변경됨: \(getDeviceName(id))")
    } else {
        fputs("오류: 장치 변경 실패\n", stderr); exit(1)
    }

case "info":
    guard args.count >= 3, let id = AudioDeviceID(args[2]) else {
        fputs("오류: 장치 ID 필요\n", stderr); exit(1)
    }
    print("이름: \(getDeviceName(id))")
    print("UID:  \(getDeviceUID(id))")
    let subs = getSubDeviceIDs(id)
    if subs.isEmpty {
        print("서브 장치: 없음")
    } else {
        print("서브 장치:")
        for sub in subs {
            print("  \(sub)\t\(getDeviceName(sub))")
        }
    }

case "create-multi":
    guard args.count >= 4 else {
        fputs("오류: 두 개 이상의 장치 ID 필요\n", stderr); exit(1)
    }
    let ids = args[2...].compactMap { AudioDeviceID($0) }
    guard ids.count >= 2 else {
        fputs("오류: 유효한 장치 ID가 부족합니다.\n", stderr); exit(1)
    }
    if let newID = createMultiOutputDevice(deviceIDs: ids) {
        print(newID)
    } else {
        fputs("오류: Multi-Output Device 생성 실패\n", stderr); exit(1)
    }

case "destroy":
    guard args.count >= 3, let id = AudioDeviceID(args[2]) else {
        fputs("오류: 장치 ID 필요\n", stderr); exit(1)
    }
    if destroyAggregateDevice(id) {
        print("삭제됨: \(id)")
    } else {
        fputs("오류: 삭제 실패\n", stderr); exit(1)
    }

default:
    fputs("알 수 없는 명령: \(args[1])\n", stderr); exit(1)
}
