import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var engineUp = false
    @Published var xcodeOK = false
    @Published var loggedIn = false
    @Published var appleId: String = ""
    @Published var team: String?
    @Published var devices: [EngineClient.Device] = []

    // Login form
    @Published var emailField = ""
    @Published var passwordField = ""
    @Published var needs2fa = false
    @Published var codeField = ""
    @Published var busy = false
    @Published var error: String?

    private var timer: Timer?

    func start() {
        EngineProcess.shared.startIfNeeded()
        // Poll health + devices on a light cadence.
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
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
            if let a = h.appleId { appleId = a }
            devices = (try? await EngineClient.devices()) ?? devices
        } catch {
            engineUp = false
        }
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
