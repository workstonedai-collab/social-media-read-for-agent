import Foundation
import Vision
import ImageIO

// Local-only OCR. No network requests or model keys.
func output(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
}
guard CommandLine.arguments.count == 2 else {
    output(["status": "invalid_input"]); exit(2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
      let width = props[kCGImagePropertyPixelWidth] as? Int,
      let height = props[kCGImagePropertyPixelHeight] as? Int,
      width > 0, height > 0, width <= 20000, height <= 30000,
      width * height <= 60000000 else {
    output(["status": "image_budget_exceeded"]); exit(2)
}
let options: [CFString: Any] = [
    kCGImageSourceCreateThumbnailFromImageAlways: true,
    kCGImageSourceCreateThumbnailWithTransform: true,
    kCGImageSourceThumbnailMaxPixelSize: 10000
]
guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
    output(["status": "image_decode_failed"]); exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
request.usesLanguageCorrection = true
if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = true }
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let rows = (request.results ?? []).compactMap { observation -> [String: Any]? in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": text.string, "confidence": text.confidence,
                "box": [box.origin.x, box.origin.y, box.size.width, box.size.height]]
    }
    output(["status": rows.isEmpty ? "no_text_detected" : "success", "lines": rows,
            "width": width, "height": height, "resized": max(width, height) > 10000])
} catch {
    output(["status": "ocr_failed"]); exit(2)
}
