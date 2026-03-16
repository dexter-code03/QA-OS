package Object Repository.AddressScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class input_State {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressScreen/input_State')
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.ACCESSIBILITY_ID)
        to.setSelectorValue('stateInput') // Assuming accessibility ID for State input
        return to
    }
}