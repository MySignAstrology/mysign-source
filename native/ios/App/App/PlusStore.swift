import Foundation
import StoreKit

// My Sign Plus through the App Store (StoreKit 2). This file knows nothing
// about Capacitor or the web page, so the tests can drive it directly
// (AppTests/PlusStoreTests.swift); PlusStorePlugin.swift is the thin layer the
// page talks to.
//
// The rule that shapes it: a transaction is finished only after the server has
// said it has it on record. Until then StoreKit keeps handing it back (at next
// launch, through Transaction.updates), so a purchase can't be lost to a dropped
// connection. Which products exist is the server's to say (engine/plan.py);
// nothing here names one.

/// A transaction as the server wants it: Apple's signed form, plus enough to
/// finish it afterwards.
struct SignedTransaction {
    let id: UInt64
    let originalID: UInt64
    let productID: String
    let jws: String
    let expires: Date?
    let revoked: Date?
    let accountToken: UUID?
    let isTrial: Bool

    var json: [String: Any] {
        var out: [String: Any] = [
            "id": String(id),
            "originalId": String(originalID),
            "productId": productID,
            "jws": jws,
            "trial": isTrial
        ]
        if let expires { out["expires"] = Int(expires.timeIntervalSince1970 * 1000) }
        if let revoked { out["revoked"] = Int(revoked.timeIntervalSince1970 * 1000) }
        return out
    }
}

enum PurchaseOutcome {
    case purchased(SignedTransaction)
    case cancelled       // she closed the sheet
    case pending         // Ask to Buy, or the bank wants a second step
}

enum PlusStoreError: LocalizedError {
    case unknownProduct(String)
    case unverified

    var errorDescription: String? {
        switch self {
        case .unknownProduct(let id): return "The App Store doesn't have \(id)."
        case .unverified: return "The App Store couldn't verify that purchase."
        }
    }
}

actor PlusStore {
    /// The one the app uses. StoreKit hands each update to a single listener,
    /// so there must be exactly one per process: a second would take renewals
    /// and refunds the first never sees.
    static let shared = PlusStore()

    /// Transactions handed out and not yet finished, by id.
    private var open: [UInt64: Transaction] = [:]
    private var listener: Task<Void, Never>?
    private var observers: [@Sendable (SignedTransaction) -> Void] = []

    /// Renewals, refunds, Ask to Buy approvals and purchases made outside the
    /// app arrive here while it runs. Call at launch, on `shared` only. Calling
    /// it again adds someone to tell; the listener itself is started once.
    func start(onUpdate: @escaping @Sendable (SignedTransaction) -> Void) {
        observers.append(onUpdate)
        guard listener == nil else { return }
        listener = Task { [weak self] in
            for await result in Transaction.updates {
                guard let self else { return }
                await self.received(result)
            }
        }
    }

    private func received(_ result: VerificationResult<Transaction>) {
        guard let signed = hold(result) else { return }
        for tell in observers { tell(signed) }
    }

    // ------------------------------------------------------------ what's for sale

    /// The products, in the order asked for, as the page shows them: the
    /// price and period come from the App Store, in her storefront's currency.
    func products(ids: [String]) async throws -> [[String: Any]] {
        let found = try await Product.products(for: ids)
        var out: [[String: Any]] = []
        for id in ids {
            guard let product = found.first(where: { $0.id == id }) else { continue }
            out.append(await describe(product))
        }
        return out
    }

    private func describe(_ product: Product) async -> [String: Any] {
        var out: [String: Any] = [
            "id": product.id,
            "name": product.displayName,
            "description": product.description,
            "price": product.displayPrice,
            "amount": NSDecimalNumber(decimal: product.price).stringValue
        ]
        guard let sub = product.subscription else { return out }
        out["period"] = period(sub.subscriptionPeriod, product: product)
        // What a year costs per month, rounded up so it is never understated.
        if sub.subscriptionPeriod.unit == .year, sub.subscriptionPeriod.value == 1 {
            var monthly = product.price / 12, rounded = Decimal()
            NSDecimalRound(&rounded, &monthly, 2, .up)
            out["perMonth"] = rounded.formatted(product.priceFormatStyle)
        }
        if let intro = sub.introductoryOffer, intro.paymentMode == .freeTrial {
            var trial = period(intro.period, product: product)
            // Apple gives the trial once per person per subscription group.
            trial["eligible"] = await sub.isEligibleForIntroOffer
            out["trial"] = trial
        }
        return out
    }

    private func period(_ p: Product.SubscriptionPeriod, product: Product) -> [String: Any] {
        let unit: String
        switch p.unit {
        case .day: unit = "day"
        case .week: unit = "week"
        case .month: unit = "month"
        case .year: unit = "year"
        @unknown default: unit = "period"
        }
        var out: [String: Any] = ["unit": unit, "value": p.value, "days": days(p)]
        if #available(iOS 16.0, *) {
            out["label"] = p.unit.formatted(product.subscriptionPeriodUnitFormatStyle)
            out["length"] = p.formatted(product.subscriptionPeriodFormatStyle)
        }
        return out
    }

    private func days(_ p: Product.SubscriptionPeriod) -> Int {
        switch p.unit {
        case .day: return p.value
        case .week: return p.value * 7
        case .month: return p.value * 30
        case .year: return p.value * 365
        @unknown default: return 0
        }
    }

    // ------------------------------------------------------------------- buying

    /// Buy one product for one account. `accountToken` is the UUID the server
    /// keeps for her; Apple signs it into the transaction and every later
    /// notification, which is how the purchase finds her account.
    func purchase(productID: String, accountToken: UUID) async throws -> PurchaseOutcome {
        guard let product = try await Product.products(for: [productID]).first else {
            throw PlusStoreError.unknownProduct(productID)
        }
        switch try await product.purchase(options: [.appAccountToken(accountToken)]) {
        case .success(let result):
            guard let signed = hold(result) else { throw PlusStoreError.unverified }
            return .purchased(signed)
        case .userCancelled:
            return .cancelled
        case .pending:
            return .pending
        @unknown default:
            return .pending
        }
    }

    /// The server has it: tell StoreKit to stop handing it back.
    @discardableResult
    func finish(id: UInt64) async -> Bool {
        if let held = open.removeValue(forKey: id) {
            await held.finish()
            return true
        }
        for await result in Transaction.unfinished {
            if case .verified(let transaction) = result, transaction.id == id {
                await transaction.finish()
                return true
            }
        }
        return false
    }

    // ---------------------------------------------------------- what she has

    /// Everything she is entitled to now, and anything bought but not yet
    /// confirmed by the server. Read at launch and after a restore.
    func entitlements() async -> [SignedTransaction] {
        var seen: [UInt64: SignedTransaction] = [:]
        for await result in Transaction.currentEntitlements {
            if let signed = hold(result) { seen[signed.id] = signed }
        }
        for await result in Transaction.unfinished {
            if let signed = hold(result) { seen[signed.id] = signed }
        }
        return seen.values.sorted { $0.id < $1.id }
    }

    /// "Restore purchases": ask the App Store afresh (this can show Apple's
    /// sign-in), then report what she has. `refresh` is replaceable so tests
    /// don't put up a system prompt.
    func restore(refresh: () async throws -> Void = { try await AppStore.sync() }) async throws -> [SignedTransaction] {
        try await refresh()
        return await entitlements()
    }

    /// Unverified results are dropped here and never reach the server, which
    /// checks the signature again itself (app/apple.py).
    private func hold(_ result: VerificationResult<Transaction>) -> SignedTransaction? {
        guard case .verified(let transaction) = result else { return nil }
        open[transaction.id] = transaction
        var trial = false
        if #available(iOS 17.2, *) {
            trial = transaction.offer?.type == .introductory
        } else {
            trial = transaction.offerType == .introductory
        }
        return SignedTransaction(
            id: transaction.id,
            originalID: transaction.originalID,
            productID: transaction.productID,
            jws: result.jwsRepresentation,
            expires: transaction.expirationDate,
            revoked: transaction.revocationDate,
            accountToken: transaction.appAccountToken,
            isTrial: trial)
    }
}
