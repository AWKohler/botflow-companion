import Foundation

// Launches/stops the Python engine that does the real work.
//
// Resolution order:
//   1. A self-contained engine bundled inside the .app at
//      Contents/Resources/engine/botflow-engine (produced by PyInstaller via
//      scripts/package-companion.sh) — this is what a downloaded build ships.
//   2. The dev checkout's venv (BOTFLOW_ENGINE_DIR or ~/Documents/...). Used
//      when running from source.
final class EngineProcess {
    static let shared = EngineProcess()
    private var proc: Process?

    /// A frozen single-binary engine bundled in the app, if present.
    private var bundledEngine: String? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let exe = res.appendingPathComponent("engine/botflow-engine").path
        return FileManager.default.isExecutableFile(atPath: exe) ? exe : nil
    }

    // Dev paths (override with BOTFLOW_ENGINE_DIR).
    private var engineDir: String {
        ProcessInfo.processInfo.environment["BOTFLOW_ENGINE_DIR"]
            ?? (NSHomeDirectory() + "/Documents/botflow-companion/engine")
    }
    private var python: String { engineDir + "/.venv/bin/python" }
    private var script: String { engineDir + "/companion.py" }

    func startIfNeeded() {
        guard proc == nil else { return }

        let p = Process()
        if let frozen = bundledEngine {
            // Shipped build: run the self-contained engine directly.
            p.executableURL = URL(fileURLWithPath: frozen)
            p.arguments = []
            p.currentDirectoryURL = URL(fileURLWithPath: (frozen as NSString).deletingLastPathComponent)
        } else {
            // Dev build: run companion.py from the checkout's venv.
            guard FileManager.default.fileExists(atPath: python),
                  FileManager.default.fileExists(atPath: script) else {
                NSLog("[BotflowCompanion] engine not found (no bundled engine; dev path \(engineDir) missing)")
                return
            }
            p.executableURL = URL(fileURLWithPath: python)
            p.arguments = ["-u", script]   // -u: unbuffered so logs flush
            p.currentDirectoryURL = URL(fileURLWithPath: engineDir)
        }

        // Capture engine stdout/stderr to a log file (otherwise it's /dev/null
        // and we're blind to startup errors).
        let logDir = NSHomeDirectory() + "/Library/Logs/BotflowCompanion"
        try? FileManager.default.createDirectory(atPath: logDir, withIntermediateDirectories: true)
        let logPath = logDir + "/engine.log"
        FileManager.default.createFile(atPath: logPath, contents: nil)
        if let handle = FileHandle(forWritingAtPath: logPath) {
            p.standardOutput = handle
            p.standardError = handle
        }
        do {
            try p.run()
            proc = p
            NSLog("[BotflowCompanion] engine started (pid \(p.processIdentifier)), log: \(logPath)")
        } catch {
            NSLog("[BotflowCompanion] engine start failed: \(error)")
        }
    }

    func stop() {
        proc?.terminate()
        proc = nil
    }
}
