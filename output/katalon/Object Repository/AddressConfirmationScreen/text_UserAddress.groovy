package Object Repository.AddressConfirmationScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class text_UserAddress {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressConfirmationScreen/text_UserAddress')
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.ACCESSIBILITY_ID)
        to.setSelectorValue('userAddressText') // Assuming accessibility ID for user's address on confirmation screen
        return to
    }
}