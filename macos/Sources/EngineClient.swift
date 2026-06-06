import Foundation

// Thin async client for the local companion engine (127.0.0.1:17321).
struct EngineClient {
    static let base = URL(string: "http://127.0.0.1:17321")!

    struct Health: Decodable {
        let ok: Bool
        let xcode: Bool
        let loggedIn: Bool
        let appleId: String?
        let team: String?
    }
    struct Device: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let osVersion: String
        let type: String
    }
    struct DevicesResponse: Decodable { let devices: [Device] }
    struct Event: Decodable, Identifiable, Hashable {
        let seq: Int
        let at: Double
        let kind: String      // info | progress | success | warning | error
        let title: String
        let message: String
        var id: Int { seq }
    }
    struct EventsResponse: Decodable { let events: [Event]; let cursor: Int }
    struct LoginResponse: Decodable {
        let ok: Bool?
        let needs2fa: Bool?
        let type: String?
        let team: String?
        let error: String?
    }

    private static func get<T: Decodable>(_ path: String, timeout: TimeInterval = 8) async throws -> T {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.timeoutInterval = timeout
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private static func post<T: Decodable>(_ path: String, body: [String: Any], timeout: TimeInterval = 30) async throws -> T {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.timeoutInterval = timeout
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(T.self, from: data)
    }

    static func health() async throws -> Health { try await get("/botflow/v1/health", timeout: 3) }
    static func devices() async throws -> [Device] {
        (try await get("/botflow/v1/devices", timeout: 20) as DevicesResponse).devices
    }
    static func events(since: Int) async throws -> EventsResponse {
        try await get("/botflow/v1/events?since=\(since)", timeout: 4)
    }
    static func login(appleId: String, password: String) async throws -> LoginResponse {
        try await post("/botflow/v1/auth/login", body: ["appleId": appleId, "password": password], timeout: 60)
    }
    static func submit2fa(code: String) async throws -> LoginResponse {
        try await post("/botflow/v1/auth/2fa", body: ["code": code], timeout: 60)
    }
    static func logout() async throws { let _: LoginResponse = try await post("/botflow/v1/auth/logout", body: [:]) }
}
