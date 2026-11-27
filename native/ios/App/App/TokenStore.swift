import Capacitor
import Foundation
import Security

// The session token, in the Keychain. It stays on this device and out of backups.
@objc(TokenStore)
public class TokenStore: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "TokenStore"
    public let jsName = "TokenStore"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "get", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "set", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clear", returnType: CAPPluginReturnPromise)
    ]

    private let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "app.mysign.ios.session",
        kSecAttrAccount as String: "session"
    ]

    @objc func get(_ call: CAPPluginCall) {
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: AnyObject?
        guard SecItemCopyMatching(lookup as CFDictionary, &found) == errSecSuccess,
              let data = found as? Data, let token = String(data: data, encoding: .utf8) else {
            call.resolve([:])
            return
        }
        call.resolve(["token": token])
    }

    @objc func set(_ call: CAPPluginCall) {
        guard let token = call.getString("token"), !token.isEmpty else {
            call.reject("token is required")
            return
        }
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = Data(token.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        if status == errSecSuccess {
            call.resolve()
        } else {
            call.reject("keychain error \(status)")
        }
    }

    @objc func clear(_ call: CAPPluginCall) {
        SecItemDelete(query as CFDictionary)
        call.resolve()
    }
}
