package Object Repository.AddressScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class input_StreetName {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressScreen/input_StreetName')
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.ACCESSIBILITY_ID)
        to.setSelectorValue('streetNameInput') // Assuming accessibility ID for Street Name input
        return to
    }
}