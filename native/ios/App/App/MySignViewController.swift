import Capacitor
import UIKit

class MySignViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(TokenStore())
        bridge?.registerPluginInstance(PlusStorePlugin())
        bridge?.registerPluginInstance(PushPlugin())
    }
}
