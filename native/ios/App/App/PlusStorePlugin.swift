import Capacitor
import StoreKit
import UIKit

// The page's door to the App Store. PlusStore.swift does the work; this only
// translates between its Swift and the page's JSON (native/bridge.js is the
// other side). The page decides nothing about money: it passes the signed
// transaction to the server and calls `finish` only when the server says so.
@objc(PlusStorePlugin)
public class PlusStorePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PlusStorePlugin"
    public let jsName = "PlusStore"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "products", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "purchase", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "finish", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "entitlements", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "restore", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "manage", returnType: CAPPluginReturnPromise)
    ]

    private let store = PlusStore.shared

    override public func load() {
        // Renewals and purchases made while the page wasn't asking. Kept until
        // the page is listening, so one that arrives during launch isn't lost.
        let store = self.store
        Task { [weak self] in
            await store.start { signed in
                self?.notifyListeners("transaction", data: signed.json, retainUntilConsumed: true)
            }
        }
    }

    @objc func products(_ call: CAPPluginCall) {
        let ids = (call.getArray("ids") ?? []).compactMap { $0 as? String }
        Task {
            do {
                call.resolve(["products": try await store.products(ids: ids)])
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }

    @objc func purchase(_ call: CAPPluginCall) {
        guard let productID = call.getString("productId"),
              let token = UUID(uuidString: call.getString("appAccountToken") ?? "") else {
            call.reject("productId and appAccountToken are required")
            return
        }
        Task {
            do {
                switch try await store.purchase(productID: productID, accountToken: token) {
                case .purchased(let signed):
                    call.resolve(["outcome": "purchased", "transaction": signed.json])
                case .cancelled:
                    call.resolve(["outcome": "cancelled"])
                case .pending:
                    call.resolve(["outcome": "pending"])
                }
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }

    @objc func finish(_ call: CAPPluginCall) {
        guard let id = UInt64(call.getString("id") ?? "") else {
            call.reject("id is required")
            return
        }
        Task {
            call.resolve(["finished": await store.finish(id: id)])
        }
    }

    @objc func entitlements(_ call: CAPPluginCall) {
        Task {
            call.resolve(["transactions": await store.entitlements().map(\.json)])
        }
    }

    @objc func restore(_ call: CAPPluginCall) {
        Task {
            do {
                call.resolve(["transactions": try await store.restore().map(\.json)])
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }

    // Apple's own sheet for changing or cancelling a subscription. The app
    // can't cancel one itself; this is the nearest it can take her.
    @objc func manage(_ call: CAPPluginCall) {
        Task { @MainActor in
            guard let scene = self.bridge?.viewController?.view.window?.windowScene else {
                call.reject("no window")
                return
            }
            do {
                try await AppStore.showManageSubscriptions(in: scene)
                call.resolve()
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }
}
