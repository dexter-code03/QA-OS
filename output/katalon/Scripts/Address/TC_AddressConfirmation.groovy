/**
 * Test Case: TC_AddressConfirmation
 * Description: Verifies the address confirmation screen functionality, including
 *              displaying user vs. Melissa addresses, and options to proceed or go back.
 * FRD Reference: Section 5.2 - "A confirmation screen is shown to the user."
 *                "The confirmation screen provides two options..."
 */

import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.annotation.SetUp
import com.kms.katalon.core.annotation.TearDown
import com.kms.katalon.core.util.KeywordUtil

class TC_AddressConfirmation {

    /**
     * SetUp method to initialize the mobile application before each test.
     * Assumes the app starts on or navigates to the address entry screen.
     */
    @SetUp
    def setUp() {
        KeywordUtil.logInfo("Starting application...")
        Mobile.startApplication('your_application_id', true) // Replace 'your_application_id'
        Mobile.waitForApplicationToLoad(10)
        // Navigate to Address Entry Screen if not default
        // Mobile.tap(findTestObject('Object Repository/Mobile/HomeScreen/btn_StartOnboarding'), 10)
        // Mobile.tap(findTestObject('Object Repository/Mobile/OnboardingScreen/btn_EnterAddress'), 10)
        KeywordUtil.logInfo("Navigated to Address Entry Screen.")
    }

    /**
     * TearDown method to close the mobile application after each test.
     */
    @TearDown
    def tearDown() {
        KeywordUtil.logInfo("Closing application...")
        Mobile.closeApplication()
    }

    /**
     * Helper method to get to the confirmation screen.
     * This simulates entering an address, selecting it, then making a slight edit
     * that triggers the confirmation screen (e.g., changing a unit number slightly).
     */
    def navigateToConfirmationScreen(String initialAddress, String editedUnit) {
        KeywordUtil.logInfo("Navigating to Confirmation Screen...")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), initialAddress, 10)
        Mobile.hideKeyboard()
        Mobile.delay(4) // Wait for suggestions
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 10) // Select the first suggestion

        // Edit an allowed field to trigger the confirmation screen
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_Unit'), editedUnit, 10)
        Mobile.hideKeyboard()
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/btn_Continue'), 10)
        Mobile.delay(3) // Wait for verification API call and screen transition
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressConfirmationScreen/lbl_UserAddress'), 10)
        KeywordUtil.logInfo("Successfully navigated to Confirmation Screen.")
    }

    /**
     * Test steps for verifying the address confirmation screen.
     */
    @com.kms.katalon.core.annotation.TestCase
    def testAddressConfirmationFlow() {
        Keyword