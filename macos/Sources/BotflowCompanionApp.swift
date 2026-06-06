import SwiftUI

// Start the engine the moment the app launches — the browser's "Run on iPhone"
// must reach it even if the user never opens the menu-bar popover.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        EngineProcess.shared.startIfNeeded()
    }
    func applicationWillTerminate(_ notification: Notification) {
        EngineProcess.shared.stop()
    }
}

@main
struct BotflowCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        MenuBarExtra("Botflow Companion", systemImage: "iphone.gen3") {
            CompanionView(model: model)
                .frame(width: 320)
                .onAppear { model.start() }
        }
        .menuBarExtraStyle(.window)
    }
}

struct CompanionView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Divider()
            appleSection
            Divider()
            deviceSection
            Divider()
            activitySection
            Divider()
            footer
        }
        .padding(14)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.engineUp ? .green : .secondary)
                .frame(width: 8, height: 8)
            Text("Botflow Companion").font(.headline)
            Spacer()
            Text(model.engineUp ? "Running" : "Starting…")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var appleSection: some View {
        if model.loggedIn {
            VStack(alignment: .leading, spacing: 4) {
                Label("Signed in to Apple", systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green).font(.subheadline)
                Text(model.appleId).font(.caption).foregroundStyle(.secondary)
                if let team = model.team {
                    Text("Team: \(team)").font(.caption).foregroundStyle(.secondary)
                }
                Button("Sign out") { Task { await model.signOut() } }
                    .buttonStyle(.link).font(.caption)
            }
        } else if model.needs2fa {
            VStack(alignment: .leading, spacing: 8) {
                Text("Two-factor code").font(.subheadline.bold())
                Text("Enter the code from your trusted device.")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("123456", text: $model.codeField).textFieldStyle(.roundedBorder)
                Button(model.busy ? "Verifying…" : "Verify") { Task { await model.verify2fa() } }
                    .disabled(model.busy || model.codeField.isEmpty)
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Sign in to Apple").font(.subheadline.bold())
                Text("Used only to sign builds for your device. Sent only to Apple.")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("Apple ID email", text: $model.emailField).textFieldStyle(.roundedBorder)
                SecureField("Password", text: $model.passwordField).textFieldStyle(.roundedBorder)
                Button(model.busy ? "Signing in…" : "Sign In") { Task { await model.signIn() } }
                    .disabled(model.busy || model.emailField.isEmpty || model.passwordField.isEmpty)
            }
        }
        if let err = model.error {
            Text(err).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var deviceSection: some View {
        Text("Devices").font(.subheadline.bold())
        if !model.xcodeOK {
            Text("Xcode command line tools not detected.").font(.caption).foregroundStyle(.orange)
        }
        if model.devices.isEmpty {
            Text("No iPhone detected. Connect, unlock, Trust, and enable Developer Mode.")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            ForEach(model.devices) { d in
                HStack(spacing: 8) {
                    Image(systemName: "iphone").foregroundStyle(.primary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(d.name).font(.caption.bold())
                        Text("iOS \(d.osVersion)").font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
            Text("Start installs from Botflow → Run on iPhone.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var activitySection: some View {
        Text("Activity").font(.subheadline.bold())
        if model.recentEvents.isEmpty {
            Text("Install activity will appear here (and as notifications).")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(model.recentEvents.prefix(5)) { ev in
                    HStack(alignment: .top, spacing: 6) {
                        Circle().fill(color(for: ev.kind)).frame(width: 6, height: 6).padding(.top, 4)
                        VStack(alignment: .leading, spacing: 0) {
                            Text(ev.title).font(.caption.bold())
                            Text(ev.message).font(.caption2).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer()
                    }
                }
            }
        }
    }

    private func color(for kind: String) -> Color {
        switch kind {
        case "success": return .green
        case "error": return .red
        case "warning": return .orange
        case "progress": return .blue
        default: return .secondary
        }
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Quit") {
                EngineProcess.shared.stop()
                NSApplication.shared.terminate(nil)
            }.buttonStyle(.borderless).font(.caption)
        }
    }
}
