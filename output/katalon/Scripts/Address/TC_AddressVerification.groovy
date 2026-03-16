/**
 * Test Case: TC_AddressVerification
 * Description: Verifies the Melissa API address verification logic, including
 *              passing AV23+ codes, failing below AV23 codes, and the retry flow.
 * FRD Reference: Section 5.2 - Functional Flow (ResultCode AV23 or higher / below AV23)
 */

import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.annotation.SetUp
import com.kms.katalon.core.annotation.TearDown
import com.kms.katalon.core.util.KeywordUtil

class TC_AddressVerification {

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
     * Test steps for verifying address verification logic.
     */
    @com.kms.katalon.core.annotation.TestCase
    def testAddressVerificationFlow() {
        KeywordUtil.logInfo("--- Starting TC_AddressVerification ---")

        String goodAddress = "100 Main St, Anytown, CA 90210" // Assumed to yield AV23+
        String badAddress = "123 Fictional Rd, Nowhere, ZZ 00000" // Assumed to yield below AV23
        String correctedAddress = "456 Oak Ave, Smallville, NY 10001" // A verifiable address after correction

        // Scenario 1: Enter a known good address (AV23+).
        KeywordUtil.logInfo("Scenario 1: Entering a known good address for AV23+ verification.")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), goodAddress, 10)
        Mobile.hideKeyboard()
        Mobile.delay(4) // Wait for suggestions
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 10) // Select the first suggestion
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/btn_Continue'), 10)

        // Verify successful navigation to the next screen (e.g., confirmation or next onboarding step).
        // For this test, we assume 'NextOnboardingStepScreen' is the destination for verified addresses.
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/NextOnboardingStepScreen/lbl_SuccessMessage'), 10)
        KeywordUtil.logInfo("Verified successful verification (AV23+) and navigation to next step.")

        // Go back to address entry for the next scenario.
        Mobile.tap(findTestObject('Object Repository/Mobile/NextOnboardingStepScreen/btn_GoBackToAddress'), 10) // Assuming a back button
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), 10)
        KeywordUtil.logInfo("Returned to Address Entry Screen.")

        // Scenario 2: Enter a known bad/unverifiable address (below AV23).
        KeywordUtil.logInfo("Scenario 2: Entering a known bad address for below AV23 verification.")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), badAddress, 10)
        Mobile.hideKeyboard()
        Mobile.delay(4) // Wait for suggestions (or lack thereof)
        // If no suggestions, user might manually type and proceed. For this test, let's assume they proceed.
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/btn_Continue'), 10)

        // Verify error message is displayed.
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/lbl_ErrorMessage'), 10)
        Mobile.verifyElementText(findTestObject('Object Repository/Mobile/AddressScreen/lbl_ErrorMessage'), "The address you entered could not be verified. Please try again.", FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Verified error message for unverified address is displayed.")

        // Verify user is returned to the address entry/edit screen.
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), 10)
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/btn_TryAgain'), 10) // Assuming a "Try Again" button or similar
        KeywordUtil.logInfo("Verified user is returned to the address entry/edit screen.")

        // Scenario 3: Edit the address to a verifiable one and retry.
        KeywordUtil.logInfo("Scenario 3: Editing the address to a verifiable one and retrying.")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), correctedAddress, 10)
        Mobile.hideKeyboard()
        Mobile.delay(4) // Wait for suggestions
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 10) // Select the first suggestion
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/btn_Continue'), 10)

        // Verify successful verification and navigation.
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/NextOnboardingStepScreen/lbl_SuccessMessage'), 10)
        KeywordUtil.logInfo("Verified successful re-verification and navigation after correction.")

        KeywordUtil.logInfo("--- TC_AddressVerification Completed Successfully ---")
    }
}