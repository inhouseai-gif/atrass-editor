import Foundation
import Vision
import AppKit

// One sequential request worker. Receives one JSON {path} line, emits one line.
// The calling process may restrict CPUs further; no concurrent OCR tasks spawn.
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["ko-KR", "en-US"]
request.usesLanguageCorrection = false
while let line = readLine() {
    autoreleasepool {
        do {
            let input = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String:Any]
            let path = input["path"] as! String
            guard let image = NSImage(contentsOfFile:path),
                  let cg = image.cgImage(forProposedRect:nil,context:nil,hints:nil) else {
                throw NSError(domain:"ImageRead",code:1)
            }
            try VNImageRequestHandler(cgImage:cg).perform([request])
            let rows = (request.results ?? []).compactMap { r -> [String:Any]? in
                guard let c=r.topCandidates(1).first else { return nil }
                let b=r.boundingBox
                return ["text":c.string,"confidence":c.confidence,"box":[b.minX,1-b.maxY,b.width,b.height]]
            }
            let result:[String:Any] = ["path":path,"width":cg.width,"height":cg.height,"rows":rows]
            let data=try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys])
            print(String(data:data,encoding:.utf8)!)
            fflush(stdout)
        } catch {
            let result:[String:Any] = ["error":String(describing:error),"rows":[]]
            let data=try! JSONSerialization.data(withJSONObject:result,options:[.sortedKeys])
            print(String(data:data,encoding:.utf8)!);fflush(stdout)
        }
    }
}
