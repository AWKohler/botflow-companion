import Foundation
import SwiftUI
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
    @Published var engineUp = false
    @Published var xcodeOK = false
    @Published var loggedIn = false
    @Published var appleId: String = ""
    @Published var team: String?
    @Published var accountType: String?   // "free" | "paid"
    @Published var devices: [EngineClient.Device] = []

    // Login form
    @Published var emailField = ""
    @Published var passwordField = ""
    @Published var needs2fa = false
    @Published var codeField = ""
    @Published var busy = false
    @Published var error: String?

    // Activity feed (most recent first) + native notifications.
    @Published var recentEvents: [EngineClient.Event] = []
    private var lastEventSeq = 0
    private var seededEvents = false

    private var timer: Timer?

    func start() {
        EngineProcess.shared.startIfNeeded()
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        // Poll health + devices + events on a light cadence.
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
        Task { await refresh() }
    }

    func refresh() async {
        do {
            let h = try await EngineClient.health()
            engineUp = true
            xcodeOK = h.xcode
            loggedIn = h.loggedIn
            team = h.team
            accountType = h.accountType
            if let a = h.appleId { appleId = a }
            devices = (try? await EngineClient.devices()) ?? devices
            await pollEvents()
        } catch {
            engineUp = false
        }
    }

    private func pollEvents() async {
        guard let resp = try? await EngineClient.events(since: lastEventSeq) else { return }
        lastEventSeq = resp.cursor
        guard !resp.events.isEmpty else { return }
        // On first poll, just seed the log — don't fire notifications for history.
        let firstSync = !seededEvents
        seededEvents = true
        for ev in resp.events {
            recentEvents.insert(ev, at: 0)
            if !firstSync { notify(ev) }
        }
        if recentEvents.count > 50 { recentEvents.removeLast(recentEvents.count - 50) }
    }

    private func notify(_ ev: EngineClient.Event) {
        let content = UNMutableNotificationContent()
        content.title = ev.title
        content.body = ev.message
        if ev.kind == "success" || ev.kind == "error" { content.sound = .default }
        let req = UNNotificationRequest(identifier: "bf-\(ev.seq)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    func signIn() async {
        error = nil; busy = true; defer { busy = false }
        do {
            let r = try await EngineClient.login(appleId: emailField, password: passwordField)
            if let e = r.error { error = e; return }
            if r.needs2fa == true { needs2fa = true; return }
            await finishLogin(team: r.team)
        } catch { self.error = "Sign-in failed: \(error.localizedDescription)" }
    }

    func verify2fa() async {
        error = nil; busy = true; defer { busy = false }
        do {
            let r = try await EngineClient.submit2fa(code: codeField)
            if let e = r.error { error = e; return }
            await finishLogin(team: r.team)
        } catch { self.error = "Verification failed: \(error.localizedDescription)" }
    }

    private func finishLogin(team: String?) async {
        needs2fa = false
        passwordField = ""; codeField = ""
        self.team = team
        await refresh()
    }

    func signOut() async {
        try? await EngineClient.logout()
        needs2fa = false; passwordField = ""; codeField = ""
        await refresh()
    }
}
