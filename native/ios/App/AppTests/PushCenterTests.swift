import UserNotifications
import XCTest
@testable import App

// The parts of notifications that need no Apple: how a device token is
// written, where a tap lands, what iOS's permission states mean to the page,
// and that a token or a tap arriving early is not lost. Asking iOS for a real
// token and receiving a real notification need a signed build on a device.
final class PushCenterTests: XCTestCase {

    func testADeviceTokenIsSentAsLowercaseHex() {
        XCTAssertEqual(PushCenter.hex(Data([0x00, 0x0f, 0xa0, 0xff])), "000fa0ff")
        XCTAssertEqual(PushCenter.hex(Data(repeating: 0xab, count: 32)).count, 64)
    }

    func testATapLandsWhereTheNotificationSays() {
        XCTAssertEqual(PushCenter.route(from: ["url": "/app?from=push&pk=morning"]), "/app?from=push&pk=morning")
        XCTAssertEqual(PushCenter.route(from: ["url": "/app?p=c123&from=push&pk=person"]), "/app?p=c123&from=push&pk=person")
        XCTAssertEqual(PushCenter.route(from: ["url": "/app"]), "/app")
    }

    func testATapNeverLeavesTheApp() {
        for bad in ["https://example.com/app", "//example.com/app", "/application", "/app?next=//example.com",
                    "/apple", "javascript:alert(1)", "/app\\..\\x", ""] {
            XCTAssertEqual(PushCenter.route(from: ["url": bad]), "/app", bad)
        }
        XCTAssertEqual(PushCenter.route(from: [:]), "/app")
        XCTAssertEqual(PushCenter.route(from: ["url": 7]), "/app")
        XCTAssertEqual(PushCenter.route(from: ["url": "/app?" + String(repeating: "a", count: 600)]), "/app")
    }

    func testPermissionStatesAsThePageReadsThem() {
        XCTAssertEqual(PushCenter.permission(.authorized), "granted")
        XCTAssertEqual(PushCenter.permission(.provisional), "granted")
        XCTAssertEqual(PushCenter.permission(.denied), "denied")
        XCTAssertEqual(PushCenter.permission(.notDetermined), "prompt")
    }

    func testEveryoneWaitingGetsTheTokenAndIOSIsAskedOnce() {
        let center = PushCenter()
        var asked = 0
        center.askSystem = { asked += 1 }
        var got: [String] = []
        center.token { if case .success(let token) = $0 { got.append(token) } }
        center.token { if case .success(let token) = $0 { got.append(token) } }
        center.registered(Data([0x01, 0x02]))
        XCTAssertEqual(got, ["0102", "0102"])
        XCTAssertEqual(asked, 1)
    }

    func testAFailureReachesThePageInsteadOfHanging() {
        let center = PushCenter()
        center.askSystem = {}
        var failed = false
        center.token { if case .failure = $0 { failed = true } }
        center.failed(NSError(domain: "test", code: 3010))
        XCTAssertTrue(failed)
    }

    func testNoAnswerFromIOSTimesOut() {
        let center = PushCenter()
        center.askSystem = {}
        let done = expectation(description: "timed out")
        center.token(timeout: 0.1) { result in
            if case .failure = result { done.fulfill() }
        }
        wait(for: [done], timeout: 2)
    }

    func testOnlyAChangedTokenIsAnnounced() {
        let center = PushCenter()
        var announced: [String] = []
        center.onToken { announced.append($0) }
        center.registered(Data([0x01]))     // the first one answers whoever asked
        center.registered(Data([0x01]))     // the same again: nothing new
        center.registered(Data([0x02]))     // iOS changed it
        XCTAssertEqual(announced, ["02"])
    }

    func testATapBeforeThePageListensIsKeptOnce() {
        let center = PushCenter()
        center.opened(["url": "/app?sky=events&from=push&pk=sky"])
        var seen: [String] = []
        center.onOpen { seen.append($0) }
        XCTAssertEqual(seen, ["/app?sky=events&from=push&pk=sky"])
        center.opened(["url": "/app"])
        XCTAssertEqual(seen, ["/app?sky=events&from=push&pk=sky", "/app"])
        center.onOpen { seen.append("again:" + $0) }   // a second listener gets nothing old
        XCTAssertEqual(seen.count, 2)
    }
}
