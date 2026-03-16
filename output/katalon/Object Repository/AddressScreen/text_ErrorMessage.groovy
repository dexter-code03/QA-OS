package Object Repository.AddressScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class text_ErrorMessage {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressScreen/text_ErrorMessage')
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.ACCESSIBILITY_ID)
        to.setSelectorValue('errorMessageText') // Assuming accessibility ID for error message text
        return to
    }
}