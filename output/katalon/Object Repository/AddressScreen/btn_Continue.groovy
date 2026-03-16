package Object Repository.AddressScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class btn_Continue {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressScreen/btn_Continue')
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.ACCESSIBILITY_ID)
        to.setSelectorValue('continueButton') // Assuming accessibility ID for the continue button
        return to
    }
}