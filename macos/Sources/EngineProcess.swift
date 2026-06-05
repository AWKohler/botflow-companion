import Foundation

// Launches/stops the Python engine that does the real work. For the dev MVP we
// point at the repo checkout; productization will bundle the engine + a Python
// runtime inside the .app and resolve these from the bundle.
final class EngineProcess {
    static let shared = EngineProcess()
    private var proc: Process?

    // Dev paths (override with BOTFLOW_ENGINE_DIR).
    private var engineDir: String {
        ProcessInfo.processInfo.environment["BOTFLOW_ENGINE_DIR"]
            ?? (NSHomeDirectory() + "/Documents/botflow-companion/engine")
    }
    private var python: String { engineDir + "/.venv/bin/python" }
    private var script: String { engineDir + "/companion.py" }

    func startIfNeeded() {
        guard proc == nil else { return }
        guard FileManager.default.fileExists(atPath: python),
              FileManager.default.fileExists(atPath: script) else {
            NSLog("[BotflowCompanion] engine not found at \(engineDir)")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", script]   // -u: unbuffered so logs flush
        p.currentDirectoryURL = URL(fileURLWithPath: engineDir)

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
