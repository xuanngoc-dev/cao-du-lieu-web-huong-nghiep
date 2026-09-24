import Foundation
import Vision
import ImageIO

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
if path.isEmpty {
    fputs("missing image path\n", stderr)
    exit(1)
}
let url = URL(fileURLWithPath: path) as CFURL
guard let src = CGImageSourceCreateWithURL(url, nil),
      let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    fputs("no image\n", stderr)
    exit(1)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([req])
var lines: [(CGFloat, CGFloat, String)] = []
for obs in req.results ?? [] {
    guard let text = obs.topCandidates(1).first?.string else { continue }
    let box = obs.boundingBox
    lines.append((box.origin.y, box.origin.x, text))
}
lines.sort { a, b in
    if abs(a.0 - b.0) > 0.015 { return a.0 > b.0 }
    return a.1 < b.1
}
for item in lines {
    print(String(format: "%.4f\t%.4f\t%@", item.0, item.1, item.2))
}
