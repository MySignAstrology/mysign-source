import StoreKit
import StoreKitTest
import XCTest
@testable import App

// PlusStore against Xcode's local StoreKit (MySign.storekit): no App Store
// Connect, no signing team, no device. Run with:
//   xcodebuild test -project App.xcodeproj -scheme App \
//     -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
final class PlusStoreTests: XCTestCase {
    private let annual = "mysign_plus_annual"
    private let monthly = "mysign_plus_monthly"
    // One session for the whole class. A new one per test reloads the
    // configuration into the simulator's StoreKit daemon each time, and events
    // sent while that is happening (a renewal, an expiry) can go missing.
    private static var session: SKTestSession!
    private var session: SKTestSession { Self.session }
    private var store: PlusStore!
    private let inbox = Inbox()

    override class func setUp() {
        super.setUp()
        session = try? SKTestSession(configurationFileNamed: "MySign")
        session?.disableDialogs = true
    }

    override func setUp() async throws {
        try XCTSkipIf(Self.session == nil, "MySign.storekit is not in the test bundle")
        session.resetToDefaultState()
        session.disableDialogs = true
        session.clearTransactions()
        // The app this test runs inside already listens on the shared store,
        // and StoreKit gives each update to one listener, so the tests use it too.
        store = PlusStore.shared
        // Until the daemon has the configuration, the App Store has no products.
        await wait("the StoreKit configuration to load", seconds: 30) {
            ((try? await Product.products(for: [self.annual, self.monthly]))?.count ?? 0) == 2
        }
        let inbox = self.inbox
        await store.start { inbox.add($0) }
    }

    override func tearDown() async throws {
        Self.session?.clearTransactions()
    }

    /// What arrives through Transaction.updates, for the tests to wait on.
    private final class Inbox: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [SignedTransaction] = []
        func add(_ item: SignedTransaction) { lock.lock(); items.append(item); lock.unlock() }
        var all: [SignedTransaction] { lock.lock(); defer { lock.unlock() }; return items }
    }

    private func wait(_ what: String, seconds: Double = 30, until done: () async -> Bool) async {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if await done() { return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        XCTFail("timed out waiting for \(what); StoreKit had: \(await snapshot())")
    }

    /// What StoreKit says right now, for a failure message worth reading.
    private func snapshot() async -> String {
        var held: [String] = []
        for await result in Transaction.currentEntitlements {
            if case .verified(let t) = result {
                held.append("\(t.id) \(t.productID) expires in \(Int(t.expirationDate?.timeIntervalSinceNow ?? 0))s")
            }
        }
        let sent = session.allTransactions().map {
            "\($0.identifier) \($0.productIdentifier) expires in \(Int($0.expirationDate?.timeIntervalSinceNow ?? 0))s renews=\($0.autoRenewingEnabled)"
        }
        return "entitlements \(held), session \(sent), updates received \(inbox.all.map(\.id))"
    }

    /// What still gives her Plus: not expired, not refunded.
    private func live() async -> [SignedTransaction] {
        await store.entitlements().filter { $0.revoked == nil && ($0.expires ?? .distantFuture) > Date() }
    }

    private func buy(_ id: String, as token: UUID = UUID()) async throws -> SignedTransaction {
        guard case .purchased(let signed) = try await store.purchase(productID: id, accountToken: token) else {
            XCTFail("purchase of \(id) did not complete")
            throw PlusStoreError.unverified
        }
        return signed
    }

    private func payload(_ jws: String) throws -> [String: Any] {
        let parts = jws.split(separator: ".")
        XCTAssertEqual(parts.count, 3, "a JWS has three parts")
        var b64 = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        b64 += String(repeating: "=", count: (4 - b64.count % 4) % 4)
        let data = try XCTUnwrap(Data(base64Encoded: b64))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    // ------------------------------------------------------------------ products

    func testBothPlansLoadWithPricePeriodAndTrial() async throws {
        let products = try await store.products(ids: [annual, monthly])
        XCTAssertEqual(products.map { $0["id"] as? String }, [annual, monthly])

        guard products.count == 2 else { return }
        let year = products[0], month = products[1]
        XCTAssertEqual(year["price"] as? String, "$59.99")
        XCTAssertEqual(month["price"] as? String, "$9.99")
        XCTAssertEqual((year["period"] as? [String: Any])?["unit"] as? String, "year")
        XCTAssertEqual((month["period"] as? [String: Any])?["unit"] as? String, "month")
        // The App Store's own word for the period, which is what the paywall shows.
        XCTAssertEqual(((year["period"] as? [String: Any])?["label"] as? String)?.lowercased(), "year")
        XCTAssertEqual(((month["period"] as? [String: Any])?["label"] as? String)?.lowercased(), "month")
        XCTAssertEqual(year["perMonth"] as? String, "$5.00")
        XCTAssertNil(month["perMonth"])
        for product in products {
            let trial = try XCTUnwrap(product["trial"] as? [String: Any])
            XCTAssertEqual(trial["days"] as? Int, 7)
            XCTAssertEqual(trial["eligible"] as? Bool, true)
        }
    }

    func testAnUnknownProductIsLeftOutNotInvented() async throws {
        let products = try await store.products(ids: ["mysign_lifetime", monthly])
        XCTAssertEqual(products.map { $0["id"] as? String }, [monthly])
    }

    // ------------------------------------------------------------------ purchase

    func testPurchaseStartsTheTrialAndCarriesTheAccountToken() async throws {
        let token = UUID()
        let signed = try await buy(annual, as: token)

        XCTAssertEqual(signed.productID, annual)
        XCTAssertEqual(signed.accountToken, token)
        XCTAssertTrue(signed.isTrial)
        let trialEnd = try XCTUnwrap(signed.expires)
        XCTAssertEqual(trialEnd.timeIntervalSinceNow, 7 * 86_400, accuracy: 3_600, "the first period is the 7-day trial")

        // What the server will read out of the signed form.
        let claims = try payload(signed.jws)
        XCTAssertEqual(claims["productId"] as? String, annual)
        XCTAssertEqual(claims["bundleId"] as? String, "app.mysign.ios")
        XCTAssertEqual((claims["appAccountToken"] as? String)?.lowercased(), token.uuidString.lowercased())
        XCTAssertEqual(claims["environment"] as? String, "Xcode")

        // For checking the server against a real Xcode-signed transaction (docs/ios-iap.md).
        if let out = ProcessInfo.processInfo.environment["MYSIGN_JWS_OUT"] {
            try signed.jws.write(toFile: out, atomically: true, encoding: .utf8)
        }
    }

    func testATransactionStaysUnfinishedUntilTheServerHasConfirmed() async throws {
        let signed = try await buy(monthly)

        var unfinished: [UInt64] = []
        for await result in Transaction.unfinished {
            if case .verified(let transaction) = result { unfinished.append(transaction.id) }
        }
        XCTAssertEqual(unfinished, [signed.id], "purchase() must not finish it")
        // A fresh launch before the server answered: it is handed back.
        let relaunched = PlusStore()
        let again = await relaunched.entitlements()
        XCTAssertEqual(again.map(\.id), [signed.id])

        let finished = await store.finish(id: signed.id)
        XCTAssertTrue(finished)
        for await _ in Transaction.unfinished { XCTFail("nothing should be left unfinished") }
        // Finished is not the same as gone: she still has Plus.
        let held = await store.entitlements()
        XCTAssertEqual(held.map(\.productID), [monthly])
    }

    func testTheTrialIsOfferedOncePerPerson() async throws {
        _ = try await buy(monthly)
        let products = try await store.products(ids: [annual, monthly])
        for product in products {
            XCTAssertEqual((product["trial"] as? [String: Any])?["eligible"] as? Bool, false)
        }
    }

    // ------------------------------------------------------------------ renewal

    func testARenewalReachesTheAppAsANewTransactionOnTheSameSubscription() async throws {
        let first = try await buy(monthly)
        await store.finish(id: first.id)

        try session.forceRenewalOfSubscription(productIdentifier: monthly)
        // Usually the updates listener is told at once. Under Xcode's test
        // session it sometimes isn't, and then the renewal is found the way
        // the app finds it at launch and on return: by asking what she holds.
        var renewal: SignedTransaction?
        await wait("the renewal") {
            renewal = self.inbox.all.last { $0.id != first.id }
            if renewal == nil { renewal = await self.store.entitlements().last { $0.id != first.id } }
            return renewal != nil
        }

        let renewed = try XCTUnwrap(renewal)
        XCTAssertEqual(renewed.originalID, first.originalID, "the same subscription")
        XCTAssertEqual(renewed.productID, monthly)
        XCTAssertFalse(renewed.isTrial, "the trial is over once it renews")
        XCTAssertGreaterThan(try XCTUnwrap(renewed.expires), try XCTUnwrap(first.expires))
        XCTAssertEqual(renewed.accountToken, first.accountToken, "Apple keeps the token on renewals")
    }

    // ------------------------------------------------------------------ restore

    func testRestoreFindsThePlanOnAFreshInstall() async throws {
        let token = UUID()
        let bought = try await buy(annual, as: token)
        await store.finish(id: bought.id)

        // AppStore.sync() can put up Apple's sign-in, so the refresh is stubbed.
        let fresh = PlusStore()
        let restored = try await fresh.restore(refresh: {})
        XCTAssertEqual(restored.map(\.productID), [annual])
        XCTAssertEqual(restored.first?.accountToken, token)
        XCTAssertEqual(restored.first?.originalID, bought.originalID)
    }

    func testRestoreWithNothingBoughtFindsNothing() async throws {
        let restored = try await store.restore(refresh: {})
        XCTAssertTrue(restored.isEmpty)
    }

    // ------------------------------------------------------------- refund, expiry

    func testARefundIsSeenAsARevocationAndEndsTheEntitlement() async throws {
        let bought = try await buy(monthly)
        await store.finish(id: bought.id)

        try session.refundTransaction(identifier: UInt(bought.id))
        // From the listener, or failing that from her history (see the renewal test).
        var found: (originalID: UInt64, jws: String)?
        await wait("the refund") {
            if let told = self.inbox.all.last(where: { $0.revoked != nil }) {
                found = (told.originalID, told.jws)
            } else {
                for await result in Transaction.all {
                    if case .verified(let t) = result, t.revocationDate != nil { found = (t.originalID, result.jwsRepresentation) }
                }
            }
            return found != nil
        }
        let refund = try XCTUnwrap(found)
        XCTAssertEqual(refund.originalID, bought.originalID)
        XCTAssertNotNil(try payload(refund.jws)["revocationDate"], "the server sees the refund in the signed form")
        let held = await live()
        XCTAssertTrue(held.isEmpty)
    }

    func testAnExpiredSubscriptionIsNoLongerAnEntitlement() async throws {
        let bought = try await buy(monthly)
        await store.finish(id: bought.id)
        try session.expireSubscription(productIdentifier: monthly)
        // StoreKit may hand the ended transaction back once more (the server is
        // told about it like any other); what must go is the live entitlement.
        await wait("the expiry") { await self.live().isEmpty }
    }

    // ------------------------------------------------------------------ changing plan

    func testMovingFromMonthlyToAnnualIsOneSubscriptionNotTwo() async throws {
        let token = UUID()
        let month = try await buy(monthly, as: token)
        await store.finish(id: month.id)
        let year = try await buy(annual, as: token)
        await store.finish(id: year.id)

        let held = await store.entitlements()
        XCTAssertEqual(held.map(\.productID), [annual], "one group, one subscription at a time")
    }
}
