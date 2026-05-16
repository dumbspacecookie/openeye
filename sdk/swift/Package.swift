// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OpenEye",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
        .visionOS(.v1),
    ],
    products: [
        .library(name: "OpenEye", targets: ["OpenEye"]),
    ],
    targets: [
        .target(
            name: "OpenEye",
            path: "Sources/OpenEye"
        ),
        .testTarget(
            name: "OpenEyeTests",
            dependencies: ["OpenEye"],
            path: "Tests/OpenEyeTests"
        ),
    ]
)
