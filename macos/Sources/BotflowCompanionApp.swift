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
                .frame(width: 360)
                .onAppear { model.start() }
        }
        .menuBarExtraStyle(.window)
    }
}

struct CompanionView: View {
    @ObservedObject var model: AppModel
    @State private var showAccountDetails = false

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

    // MARK: Header

    private var header: some View {
        HStack(spacing: 9) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable().interpolation(.high)
                .frame(width: 22, height: 22)
                .clipShape(RoundedRectangle(cornerRadius: 5))
            Text("Botflow Companion").font(.headline)
            Spacer()
            HStack(spacing: 5) {
                Circle().fill(model.engineUp ? .green : .secondary).frame(width: 7, height: 7)
                Text(model.engineUp ? "Running" : "Starting…")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Apple sign-in + account type

    @ViewBuilder private var appleSection: some View {
        if model.loggedIn {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
                    Text("Signed in to Apple").font(.subheadline.weight(.semibold))
                    Spacer()
                    accountBadge
                }
                Text(model.appleId).font(.caption).foregroundStyle(.secondary)
                if let team = model.team {
                    Text("Team · \(team)").font(.caption).foregroundStyle(.secondary)
                }
                accountTypePanel
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
                Text("A free Apple ID works — apps re-sign every 7 days. A paid account ($99/yr) signs for a year.")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
        }
        if let err = model.error {
            Text(err).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var accountBadge: some View {
        if let t = model.accountType {
            Text(t == "free" ? "Free account" : "Paid account")
                .font(.caption2.weight(.semibold))
                .padding(.horizontal, 7).padding(.vertical, 2)
                .background((t == "free" ? Color.orange : Color.green).opacity(0.18),
                            in: Capsule())
                .foregroundStyle(t == "free" ? Color.orange : Color.green)
        }
    }

    @ViewBuilder private var accountTypePanel: some View {
        if let t = model.accountType {
            VStack(alignment: .leading, spacing: 6) {
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) { showAccountDetails.toggle() }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: showAccountDetails ? "chevron.down" : "chevron.right")
                            .font(.caption2)
                        Text(t == "free"
                             ? "Free: 7-day signing · up to 3 apps · this device"
                             : "Paid: 1-year signing · 100 devices · all capabilities")
                            .font(.caption)
                    }.foregroundStyle(.secondary)
                }.buttonStyle(.plain)

                if showAccountDetails {
                    VStack(alignment: .leading, spacing: 5) {
                        accountRow("Signing validity", free: "7 days (re-run weekly)", paid: "1 year", isFree: t == "free")
                        accountRow("App limit", free: "3 apps", paid: "Unlimited", isFree: t == "free")
                        accountRow("Devices", free: "Your own", paid: "Up to 100", isFree: t == "free")
                        accountRow("Push, widgets, etc.", free: "Limited", paid: "Full", isFree: t == "free")
                        accountRow("TestFlight / App Store", free: "No", paid: "Yes", isFree: t == "free")
                        if t == "free" {
                            Link("Upgrade to the Apple Developer Program ($99/yr) →",
                                 destination: URL(string: "https://developer.apple.com/programs/")!)
                                .font(.caption2)
                        }
                    }
                    .padding(8)
                    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    private func accountRow(_ label: String, free: String, paid: String, isFree: Bool) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(label).font(.caption2).foregroundStyle(.secondary).frame(width: 120, alignment: .leading)
            Text(isFree ? free : paid)
                .font(.caption2.weight(.medium))
                .foregroundStyle(isFree ? Color.primary : Color.green)
            Spacer()
        }
    }

    // MARK: Devices

    @ViewBuilder private var deviceSection: some View {
        Text("Devices").font(.subheadline.bold())
        if !model.xcodeOK {
            Label("Xcode command-line tools not detected. Run: xcode-select --install",
                  systemImage: "exclamationmark.triangle.fill")
                .font(.caption).foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        }
        if model.devices.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text("No iPhone connected").font(.caption.weight(.medium))
                Text("Connect your iPhone with a cable, unlock it, and tap “Trust” when prompted.")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            ForEach(model.devices) { d in DeviceCard(device: d) }
        }
    }

    // MARK: Activity

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

// MARK: - Device card (status + Developer Mode guide)

struct DeviceCard: View {
    let device: EngineClient.Device
    @State private var showGuide = false

    private var ready: Bool { device.devModeEnabled }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 9) {
                Image(systemName: device.type == "ipad" ? "ipad" : "iphone")
                    .font(.title3).foregroundStyle(.primary).frame(width: 22)
                VStack(alignment: .leading, spacing: 1) {
                    Text(device.name).font(.caption.bold())
                    Text("iOS \(device.osVersion)" + transportSuffix)
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
            }

            statusRow

            if device.devModeEnabled {
                Text("Start installs from Botflow → Run on iPhone.")
                    .font(.caption2).foregroundStyle(.secondary)
            } else if device.devModeDisabled {
                devModeGuide
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.secondary.opacity(0.12), lineWidth: 1))
    }

    private var transportSuffix: String {
        switch device.transport {
        case "wired", "usb": return " · USB"
        case "localNetwork": return " · Network"
        default: return ""
        }
    }

    @ViewBuilder private var statusRow: some View {
        if device.devModeEnabled {
            Label("Developer Mode on — ready", systemImage: "checkmark.circle.fill")
                .font(.caption).foregroundStyle(.green)
        } else if device.devModeDisabled {
            Label("Developer Mode is off", systemImage: "exclamationmark.circle.fill")
                .font(.caption).foregroundStyle(.orange)
        } else if device.developerMode == "restricted" {
            Label("Developer Mode restricted by this device’s management profile",
                  systemImage: "lock.circle.fill")
                .font(.caption).foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Label("Checking Developer Mode…", systemImage: "circle.dotted")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var devModeGuide: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { showGuide.toggle() }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: showGuide ? "chevron.down" : "chevron.right").font(.caption2)
                    Text(showGuide ? "Hide setup steps" : "Set up Developer Mode")
                        .font(.caption.weight(.medium))
                }.foregroundStyle(Color.accentColor)
            }.buttonStyle(.plain)

            if showGuide {
                VStack(alignment: .leading, spacing: 6) {
                    step(1, "On your iPhone, open Settings → Privacy & Security.")
                    step(2, "Tap Developer Mode and turn it on.")
                    step(3, "Restart your iPhone when prompted.")
                    step(4, "After it restarts, tap “Turn On” and enter your passcode.")
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.small)
                        Text("Waiting for Developer Mode… this updates automatically.")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Text("Don’t see Developer Mode? Keep your iPhone plugged in and unlocked — the option only appears after it has connected to a Mac.")
                        .font(.caption2).foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(8)
                .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Text("\(n)")
                .font(.caption2.weight(.bold))
                .frame(width: 16, height: 16)
                .background(Color.accentColor.opacity(0.18), in: Circle())
                .foregroundStyle(Color.accentColor)
            Text(text).font(.caption2).fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
    }
}
