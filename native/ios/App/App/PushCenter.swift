import Capacitor
import UIKit
import UserNotifications

// Notifications from Apple's push service, for the page (PushPlugin.swift is
// its door, native/bridge.js the other side). This asks iOS for the device
// token, shows a notification that arrives while the app is open, and turns a
// tap into the place in the app it was about. It never asks permission by
// itself: only when she turns notifications on (PushPlugin.enable).
final class PushCenter: NSObject, NotificationHandlerProtocol {
    static let shared = PushCenter()

    enum PushError: LocalizedError {
        case timedOut
        var errorDescription: String? { "This iPhone didn't answer. Check your connection and try again." }
    }

    private let lock = NSLock()
    private var waiting: [(Result<String, Error>) -> Void] = []
    private var lastToken: String?
    private var pendingOpen: String?
    private var tokenListener: ((String) -> Void)?
    private var openListener: ((String) -> Void)?

    /// Asks iOS to register. Replaced in tests, where there is no Apple to ask.
    var askSystem: () -> Void = {
        DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() }
    }

    // ------------------------------------------------------------ pure parts

    /// The token the way the server keeps it: lowercase hex.
    static func hex(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }

    /// Where a tap lands: the notification's own `url` when it is a path inside
    /// the app, and the app's front door for anything else. Never a full URL,
    /// so a notification can't send the page anywhere but its own screens.
    static func route(from userInfo: [AnyHashable: Any]) -> String {
        guard let url = userInfo["url"] as? String, url.count <= 500,
              url == "/app" || url.hasPrefix("/app?") || url.hasPrefix("/app/"),
              !url.contains("//"), !url.contains("\\") else { return "/app" }
        return url
    }

    static func permission(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized, .provisional, .ephemeral: return "granted"
        case .denied: return "denied"
        case .notDetermined: return "prompt"
        @unknown default: return "prompt"
        }
    }

    // ------------------------------------------------------------- the token

    /// The device token, asked for again each time: iOS may change it, and
    /// asking is how the app finds out.
    func token(timeout: TimeInterval = 10, _ done: @escaping (Result<String, Error>) -> Void) {
        lock.lock()
        waiting.append(done)
        let first = waiting.count == 1
        lock.unlock()
        guard first else { return }
        askSystem()
        // iOS answers nothing at all while it can't reach Apple.
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
            self?.answer(.failure(PushError.timedOut))
        }
    }

    private func answer(_ result: Result<String, Error>) {
        lock.lock()
        let asked = waiting
        waiting = []
        lock.unlock()
        asked.forEach { $0(result) }
    }

    /// From AppDelegate. Also called by iOS on its own when the token changes.
    func registered(_ token: Data) {
        let now = Self.hex(token)
        lock.lock()
        let changed = lastToken != nil && lastToken != now
        lastToken = now
        let listener = tokenListener
        lock.unlock()
        answer(.success(now))
        if changed { listener?(now) }
    }

    func failed(_ error: Error) {
        answer(.failure(error))
    }

    func onToken(_ listener: @escaping (String) -> Void) {
        lock.lock(); tokenListener = listener; lock.unlock()
    }

    // --------------------------------------------------------------- the tap

    /// A tap that arrives before the page is listening (the app was closed)
    /// is kept and handed over once it is.
    func onOpen(_ listener: @escaping (String) -> Void) {
        lock.lock()
        openListener = listener
        let kept = pendingOpen
        pendingOpen = nil
        lock.unlock()
        if let kept = kept { listener(kept) }
    }

    func opened(_ userInfo: [AnyHashable: Any]) {
        let route = Self.route(from: userInfo)
        lock.lock()
        let listener = openListener
        if listener == nil { pendingOpen = route }
        lock.unlock()
        listener?(route)
    }

    // ------------------------------------- NotificationHandlerProtocol (Capacitor)

    /// The app is open: show it anyway. The morning note is the product, and
    /// iOS shows nothing in the foreground unless told to.
    func willPresent(notification: UNNotification) -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    func didReceive(response: UNNotificationResponse) {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        opened(response.notification.request.content.userInfo)
    }
}
