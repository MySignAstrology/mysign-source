import Capacitor
import UIKit
import UserNotifications

// The page's door to notifications. PushCenter.swift does the work; this only
// translates (native/bridge.js is the other side, window.MySignPush). The
// server decides what is sent and when (app/apns.py); the phone only says
// whether it may be, and which device it is.
@objc(PushPlugin)
public class PushPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PushPlugin"
    public let jsName = "NativePush"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "enable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "token", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "openSettings", returnType: CAPPluginReturnPromise)
    ]

    private let center = PushCenter.shared

    override public func load() {
        // Capacitor owns the notification delegate; this is its seat for push.
        bridge?.notificationRouter.pushNotificationHandler = center
        // Kept until the page is listening: a tap can be what launched the app.
        center.onOpen { [weak self] url in
            self?.notifyListeners("opened", data: ["url": url], retainUntilConsumed: true)
        }
        center.onToken { [weak self] token in
            self?.notifyListeners("token", data: ["token": token], retainUntilConsumed: true)
        }
    }

    /// What iOS will allow: "granted", "denied", or "prompt" (never asked).
    @objc func status(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            call.resolve(["permission": PushCenter.permission(settings.authorizationStatus)])
        }
    }

    /// She turned notifications on. This is the only place iOS is asked for
    /// permission; it shows its sheet the first time and never again.
    @objc func enable(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else {
                self.status(call)
                return
            }
            self.resolveToken(call)
        }
    }

    /// The device token, without asking for anything: nothing unless she has
    /// already allowed notifications. The page calls it at launch to catch a
    /// token iOS changed while the app was closed.
    @objc func token(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let permission = PushCenter.permission(settings.authorizationStatus)
            guard permission == "granted" else {
                call.resolve(["permission": permission])
                return
            }
            self.resolveToken(call)
        }
    }

    private func resolveToken(_ call: CAPPluginCall) {
        center.token { result in
            switch result {
            case .success(let token):
                call.resolve(["permission": "granted", "token": token])
            case .failure(let error):
                call.resolve(["permission": "granted", "error": error.localizedDescription])
            }
        }
    }

    /// iOS's own page for this app's notifications: the only way back once she
    /// has said no to the permission sheet.
    @objc func openSettings(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            let address: String
            if #available(iOS 16.0, *) {
                address = UIApplication.openNotificationSettingsURLString
            } else {
                address = UIApplication.openSettingsURLString
            }
            guard let url = URL(string: address) else {
                call.reject("no settings page")
                return
            }
            UIApplication.shared.open(url) { opened in
                call.resolve(["opened": opened])
            }
        }
    }
}
